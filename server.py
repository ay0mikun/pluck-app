"""Local moodboard API with account-scoped storage and Pillow thumbnails."""

import base64
import io
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image

import oauth
from auth import authenticate, session_user
from media import prepare_image

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("MOODBOARD_DATA_DIR", ROOT / "data"))
UPLOADS = DATA / "uploads"
THUMBNAILS = DATA / "thumbnails"
AVATARS = DATA / "avatars"
MAX_IMAGE = 10 * 1024 * 1024


@contextmanager
def db():
    con = sqlite3.connect(DATA / "moodboard.sqlite3")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        with con:
            yield con
    finally:
        con.close()


def store_image(identifier, raw):
    width, height, full, thumbnail = prepare_image(raw)
    (UPLOADS / (identifier + ".png")).write_bytes(full)
    (THUMBNAILS / (identifier + ".webp")).write_bytes(thumbnail)
    return width, height, f"/uploads/{identifier}.png", f"/thumbnails/{identifier}.webp"


def backfill_images():
    with db() as con:
        for row in con.execute(
            "SELECT id,image FROM designs WHERE width IS NULL OR thumbnail_url IS NULL"
        ).fetchall():
            file = UPLOADS / Path(row["image"]).name
            if not file.is_file():
                continue
            try:
                width, height, url, thumbnail = store_image(
                    row["id"], file.read_bytes()
                )
                con.execute(
                    "UPDATE designs SET width=?,height=?,url=?,thumbnail_url=?,image=? WHERE id=?",
                    (width, height, url, thumbnail, url, row["id"]),
                )
            except ValueError:
                print(f"Skipping unreadable legacy image {row['id']}", flush=True)


def clean(value, limit=200):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("A text field is invalid or too long.")
    return value.strip()


class ImportFailure(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def fetch_post(post_id, token=None):
    if token:
        query = urlencode(
            {
                "expansions": "attachments.media_keys,author_id",
                "media.fields": "url,type",
                "user.fields": "username,profile_image_url",
            }
        )
        request = Request(
            f"https://api.x.com/2/tweets/{post_id}?{query}",
            headers={"Authorization": f"Bearer {token}"},
        )
    else:
        request = Request(
            f"https://api.fxtwitter.com/status/{post_id}",
            headers={
                "User-Agent": "MoodboardPrototype/0.1",
                "Accept": "application/json",
            },
        )
    try:
        with build_opener(NoRedirect).open(request, timeout=20) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ImportFailure(
                502, "The post provider returned too much data. Try another post."
            )
        post = json.loads(raw)
        if not isinstance(post, dict):
            raise ValueError("Invalid post response")
    except HTTPError as error:
        code = error.code
        error.close()
        if code in (401, 403, 404):
            raise ImportFailure(
                422,
                "This post is unavailable to the importer. It may be private, deleted, or restricted.",
            )
        if code == 429:
            raise ImportFailure(
                429, "The post provider is busy. Please try again shortly."
            )
        raise ImportFailure(
            502, "The post provider could not return this post. Please try again later."
        )
    except (URLError, TimeoutError):
        raise ImportFailure(
            502, "Could not reach the post provider. Please try again shortly."
        )
    except (ValueError, TypeError):
        raise ImportFailure(
            502,
            "The post provider returned an invalid response. Please try again later.",
        )
    if token:
        data = post.get("data") or {}
        includes = post.get("includes") or {}
        photos = [
            m
            for m in includes.get("media", [])
            if m.get("type") == "photo" and m.get("url")
        ]
        author = next(
            (
                u
                for u in includes.get("users", [])
                if u.get("id") == data.get("author_id")
            ),
            {},
        )
        creator = author.get("username", "")
        avatar = author.get("profile_image_url", "")
    else:
        code = post.get("code", 200)
        if code != 200:
            raise ImportFailure(
                429 if code == 429 else 422,
                "This post is unavailable. Check that it is public and try again.",
            )
        data = post.get("tweet") or {}
        if not isinstance(data, dict):
            raise ImportFailure(
                422, "This post is unavailable. Check that it is public and try again."
            )
        photos = [
            m for m in (data.get("media") or {}).get("photos", []) if m.get("url")
        ]
        creator = (data.get("author") or {}).get("screen_name", "")
        avatar = (data.get("author") or {}).get("avatar_url", "")
    if not photos:
        raise ImportFailure(
            422,
            "This post has no accessible attached photos. Video-only and text-only posts cannot be imported yet.",
        )
    return (
        str(data.get("text") or "Imported design")[:200],
        str(creator)[:200],
        photos[:4],
        avatar,
    )


def fetch_photo(url):
    media = urlparse(url)
    if (
        media.scheme != "https"
        or media.hostname != "pbs.twimg.com"
        or media.port not in (None, 443)
        or media.username
        or not media.path.startswith("/media/")
    ):
        raise ImportFailure(
            502, "The post provider returned an unsupported image address."
        )
    try:
        with build_opener(NoRedirect).open(url, timeout=20) as response:
            raw = response.read(MAX_IMAGE + 1)
    except (URLError, TimeoutError):
        raise ImportFailure(
            502, "An attached image could not be downloaded. Please try again."
        )
    if len(raw) > MAX_IMAGE:
        raise ImportFailure(422, "An attached image exceeds the 10 MB limit.")
    return raw


def fetch_avatar(url):
    """Cache only public X profile images; a missing avatar never blocks a save."""
    if not isinstance(url, str):
        return ""
    try:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "pbs.twimg.com"
            or parsed.port not in (None, 443)
            or parsed.username
            or not parsed.path.startswith("/profile_images/")
        ):
            return ""
        with build_opener(NoRedirect).open(url, timeout=8) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            return ""
        _, _, _, thumbnail = prepare_image(raw)
        with Image.open(io.BytesIO(thumbnail)) as image:
            image.thumbnail((64, 64))
            identifier = uuid.uuid4().hex
            image.save(AVATARS / (identifier + ".webp"), "WEBP", quality=85)
        return f"/avatars/{identifier}.webp"
    except (OSError, ValueError):
        return ""


class Handler(BaseHTTPRequestHandler):
    def respond(self, status, value, cookie=None):
        body = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def oauth_redirect(self, location, cookie=None):
        self.send_response(303)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        if cookie:
            if os.environ.get("APP_ORIGIN", "").startswith("https://"):
                cookie += "; Secure"
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_request(self, code="-", size="-"):
        if self.path.startswith("/api/auth/") and "?" in self.path:
            self.log_message("%s %s %s", self.command, self.path.split("?", 1)[0], code)
        else:
            super().log_request(code, size)

    def oauth_login(self, provider, action):
        try:
            if action == "start":
                with db() as con:
                    location, cookie = oauth.start(con, provider, self)
                return self.oauth_redirect(location, cookie)
            query = parse_qs(urlparse(self.path).query)
            with db() as con:
                attempt = oauth.consume(con, provider, self, query)
            subject, email = oauth.profile(provider, self, attempt, query.get("code", [""])[0])
            with db() as con:
                con.execute("BEGIN IMMEDIATE")
                cookie = oauth.sign_in(con, provider, subject, email, attempt["user_id"])
            return self.oauth_redirect("/#library", cookie)
        except ValueError as error:
            message = str(error)
        except (OSError, KeyError, TypeError):
            message = "The provider could not complete sign-in. Please try again."
        return self.oauth_redirect("/?" + urlencode({"auth_error": message}) + "#auth")

    def do_GET(self):
        path = urlparse(self.path).path
        match = re.fullmatch(r"/api/auth/(google|x)/(start|callback)", path)
        if match:
            return self.oauth_login(*match.groups())
        if path == "/api/auth/me":
            with db() as con:
                user = session_user(self.headers, con)
            return self.respond(200, {"user": user})
        if path == "/api/state":
            with db() as con:
                user = session_user(self.headers, con)
                if not user:
                    return self.respond(401, {"error": "Sign in to open your library."})
                boards = [
                    dict(r)
                    for r in con.execute(
                        "SELECT * FROM boards WHERE owner_id=? ORDER BY name",
                        (user["id"],),
                    )
                ]
                designs = [
                    dict(r)
                    for r in con.execute(
                        "SELECT * FROM designs WHERE owner_id=? ORDER BY created DESC, rowid DESC",
                        (user["id"],),
                    )
                ]
                for design in designs:
                    design["boards"] = [
                        r[0]
                        for r in con.execute(
                            "SELECT board_id FROM board_designs WHERE design_id=?",
                            (design["id"],),
                        )
                    ]
            return self.respond(
                200, {"boards": boards, "designs": designs, "user": user}
            )
        files = {
            "/": ROOT / "index.html",
            "/index.html": ROOT / "index.html",
            "/app.js": ROOT / "app.js",
            "/styles.css": ROOT / "styles.css",
            "/landing.css": ROOT / "landing.css",
            "/landing.js": ROOT / "landing.js",
        }
        file = files.get(path)
        if path.startswith("/assets/") and "/" not in path[8:]:
            file = ROOT / "assets" / path[8:]
        if re.fullmatch(r"/uploads/[a-f0-9]{32}\.(png|jpg|gif|webp)", path):
            file = UPLOADS / path.split("/")[-1]
        if re.fullmatch(r"/thumbnails/[a-f0-9]{32}\.webp", path):
            file = THUMBNAILS / path.split("/")[-1]
        if re.fullmatch(r"/avatars/[a-f0-9]{32}\.webp", path):
            file = AVATARS / path.split("/")[-1]
        if path.startswith(("/uploads/", "/thumbnails/", "/avatars/")):
            with db() as con:
                user = session_user(self.headers, con)
                if (
                    not user
                    or not con.execute(
                        "SELECT 1 FROM designs WHERE owner_id=? AND (image=? OR url=? OR thumbnail_url=? OR creator_avatar_url=?)",
                        (user["id"], path, path, path, path),
                    ).fetchone()
                ):
                    return self.respond(404, {"error": "Not found."})
        if not file or not file.is_file():
            return self.respond(404, {"error": "Not found."})
        types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css",
            ".js": "text/javascript",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".jpg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".ttf": "font/ttf",
            ".woff2": "font/woff2",
        }
        body = file.read_bytes()
        self.send_response(200)
        self.send_header(
            "Content-Type", types.get(file.suffix, "application/octet-stream")
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.mutate("POST")

    def do_PATCH(self):
        self.mutate("PATCH")

    def do_DELETE(self):
        self.mutate("DELETE")

    def mutate(self, method):
        # Local API accepts same-origin JSON only; no cross-origin writes.
        origin = self.headers.get("Origin")
        if origin and origin not in (f"http://{self.headers.get('Host')}",):
            return self.respond(403, {"error": "Cross-origin writes are not allowed."})
        try:
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.respond(415, {"error": "Use application/json."})
            size = int(self.headers.get("Content-Length", "0"))
            if size > 15 * 1024 * 1024:
                return self.respond(413, {"error": "Image must be under 10 MB."})
            body = json.loads(self.rfile.read(size) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object.")
            path = urlparse(self.path).path
            with db() as con:
                if path == "/api/auth/continue" and method == "POST":
                    email = clean(body.get("email"), 254).lower()
                    if email.endswith("@oauth.invalid"):
                        raise ValueError("Continue with your original sign-in provider.")
                    password = body.get("password", "")
                    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                        raise ValueError("Enter a valid email address.")
                    if not isinstance(password, str) or not 8 <= len(password) <= 128:
                        raise ValueError("Use a password between 8 and 128 characters.")
                    con.execute("BEGIN IMMEDIATE")
                    create_account = not con.execute(
                        "SELECT 1 FROM users WHERE email=?", (email,)
                    ).fetchone()
                    user, cookie = authenticate(con, email, password, create_account)
                    con.commit()
                    return self.respond(200, {"user": user}, cookie)
                user = session_user(self.headers, con)
                if not user:
                    return self.respond(401, {"error": "Sign in to continue."})
                owner = user["id"]
                if path == "/api/auth/logout" and method == "POST":
                    con.execute("DELETE FROM sessions WHERE user_id=?", (owner,))
                    con.commit()
                    return self.respond(
                        200,
                        {"ok": True},
                        "moodboard_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0",
                    )
                if path == "/api/boards" and method == "POST":
                    name = clean(body.get("name"), 80)
                    if not name:
                        raise ValueError("Name your moodboard.")
                    identifier = uuid.uuid4().hex
                    con.execute(
                        "INSERT INTO boards(id,name,owner_id) VALUES(?,?,?)",
                        (identifier, name, owner),
                    )
                    con.commit()
                    return self.respond(201, {"id": identifier})
                match = re.fullmatch(r"/api/boards/([a-f0-9]{32})", path)
                if match and method in ("PATCH", "DELETE"):
                    if not con.execute(
                        "SELECT 1 FROM boards WHERE id=? AND owner_id=?",
                        (match[1], owner),
                    ).fetchone():
                        return self.respond(404, {"error": "Moodboard not found."})
                    if method == "DELETE":
                        con.execute("DELETE FROM boards WHERE id=?", (match[1],))
                    else:
                        name = clean(body.get("name"), 80)
                        if not name:
                            raise ValueError("Name your moodboard.")
                        con.execute(
                            "UPDATE boards SET name=? WHERE id=?", (name, match[1])
                        )
                    con.commit()
                    return self.respond(200, {"ok": True})
                if path == "/api/import" and method == "POST":
                    token = os.environ.get("X_BEARER_TOKEN")
                    source = clean(body.get("url"), 2000)
                    parsed = urlparse(source)
                    match = re.fullmatch(r"/[A-Za-z0-9_]+/status/(\d+)/?", parsed.path)
                    if (
                        parsed.scheme != "https"
                        or parsed.hostname
                        not in ("x.com", "www.x.com", "twitter.com", "www.twitter.com")
                        or not match
                    ):
                        raise ValueError("Enter an HTTPS link to a specific X post.")
                    source = f"https://x.com{parsed.path.rstrip('/')}"
                    existing = [
                        r[0]
                        for r in con.execute(
                            "SELECT id FROM designs WHERE source=? AND owner_id=?",
                            (source, owner),
                        )
                    ]
                    if existing:
                        return self.respond(200, {"ids": existing, "existing": True})
                    title, creator, photos, avatar = fetch_post(match[1], token)
                    avatar_url = fetch_avatar(avatar)
                    staged = [fetch_photo(photo["url"]) for photo in photos]
                    identifiers = []
                    for raw in staged:
                        identifier = uuid.uuid4().hex
                        width, height, url, thumbnail = store_image(identifier, raw)
                        con.execute(
                            "INSERT INTO designs(id,title,image,source,creator,width,height,url,thumbnail_url,owner_id,creator_avatar_url) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                identifier,
                                title,
                                url,
                                source,
                                creator,
                                width,
                                height,
                                url,
                                thumbnail,
                                owner,
                                avatar_url,
                            ),
                        )
                        identifiers.append(identifier)
                    con.commit()
                    return self.respond(201, {"ids": identifiers})
                if path == "/api/designs" and method == "POST":
                    title = clean(body.get("title"), 200)
                    if not title:
                        raise ValueError("Give the design a title.")
                    raw = base64.b64decode(body.get("image", ""), validate=True)
                    if not raw or len(raw) > MAX_IMAGE:
                        raise ValueError("Choose an image under 10 MB.")
                    source = clean(body.get("source", ""), 2000)
                    if source and urlparse(source).scheme not in ("http", "https"):
                        raise ValueError("Source must be an HTTP or HTTPS link.")
                    identifier = uuid.uuid4().hex
                    width, height, url, thumbnail = store_image(identifier, raw)
                    con.execute(
                        "INSERT INTO designs(id,title,image,source,creator,notes,width,height,url,thumbnail_url,owner_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            identifier,
                            title,
                            url,
                            source,
                            clean(body.get("creator", "")),
                            clean(body.get("notes", ""), 5000),
                            width,
                            height,
                            url,
                            thumbnail,
                            owner,
                        ),
                    )
                    con.commit()
                    return self.respond(201, {"id": identifier})
                match = re.fullmatch(r"/api/designs/([a-f0-9]{32})", path)
                if match and method in ("PATCH", "DELETE"):
                    if not con.execute(
                        "SELECT 1 FROM designs WHERE id=? AND owner_id=?",
                        (match[1], owner),
                    ).fetchone():
                        return self.respond(404, {"error": "Design not found."})
                    if method == "DELETE":
                        con.execute("DELETE FROM designs WHERE id=?", (match[1],))
                    else:
                        boards = body.get("boards")
                        if not isinstance(boards, list) or any(
                            not isinstance(b, str)
                            or not con.execute(
                                "SELECT 1 FROM boards WHERE id=? AND owner_id=?",
                                (b, owner),
                            ).fetchone()
                            for b in boards
                        ):
                            raise ValueError("Choose valid moodboards.")
                        con.execute(
                            "DELETE FROM board_designs WHERE design_id=?", (match[1],)
                        )
                        con.executemany(
                            "INSERT INTO board_designs VALUES(?,?)",
                            [(b, match[1]) for b in set(boards)],
                        )
                    con.commit()
                    return self.respond(200, {"ok": True})
            return self.respond(404, {"error": "Not found."})
        except ImportFailure as error:
            self.respond(error.status, {"error": error.message})
        except ValueError as error:
            self.respond(400, {"error": str(error) or "Invalid input."})
        except (TypeError, KeyError):
            self.respond(
                400,
                {"error": "Invalid input. Check your fields, image format, and size."},
            )
        except (URLError, TimeoutError):
            self.respond(
                502, {"error": "Could not reach X. Try again later or upload an image."}
            )
        except Exception:
            self.log_error("Unexpected server error")
            self.respond(
                500, {"error": "Could not save your changes. Please try again."}
            )


def initialize_storage():
    for directory in (DATA, UPLOADS, THUMBNAILS, AVATARS):
        directory.mkdir(parents=True, exist_ok=True)
    with db() as con:
        con.executescript("""CREATE TABLE IF NOT EXISTS boards(id TEXT PRIMARY KEY, name TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS designs(id TEXT PRIMARY KEY, title TEXT NOT NULL, image TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', creator TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '', created TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS board_designs(board_id TEXT REFERENCES boards(id) ON DELETE CASCADE, design_id TEXT REFERENCES designs(id) ON DELETE CASCADE, PRIMARY KEY(board_id,design_id));""")

    # Additive migrations preserve previously imported designs and moodboards.
    with db() as con:
        for table, columns in {
            "designs": {
                "width": "INTEGER",
                "height": "INTEGER",
                "url": "TEXT",
                "thumbnail_url": "TEXT",
                "owner_id": "TEXT",
                "creator_avatar_url": "TEXT",
            },
            "boards": {"owner_id": "TEXT"},
        }.items():
            existing = {row[1] for row in con.execute(f"PRAGMA table_info({table})")}
            for column, kind in columns.items():
                if column not in existing:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
        con.executescript(
            "CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,salt TEXT NOT NULL,password_hash TEXT NOT NULL); CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,user_id TEXT REFERENCES users(id) ON DELETE CASCADE,expires REAL NOT NULL);"
        )
    with db() as con:
        con.executescript(oauth.SCHEMA)
    backfill_images()


def load_local_env(path):
    """Read local settings without executing the file as shell code."""
    if not path.is_file():
        return
    allowed = {
        "PORT", "APP_ORIGIN", "X_BEARER_TOKEN",
        "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET",
        "X_OAUTH_CLIENT_ID", "X_OAUTH_CLIENT_SECRET",
    }
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.strip().partition("=")
        if not separator or key.strip() not in allowed:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
            value = value[1:-1]
        if value:
            os.environ.setdefault(key.strip(), value)


if __name__ == "__main__":
    load_local_env(ROOT / ".env")
    initialize_storage()
    port = int(os.environ.get("PORT", "5173"))
    print(f"Pluck running at http://127.0.0.1:{port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
