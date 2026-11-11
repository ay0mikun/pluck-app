"""Local moodboard API with account-scoped storage and Pillow thumbnails."""

import base64
import io
import json
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from PIL import Image

import database
import object_store
import email_login
import oauth
from auth import session_user, renew_session
from media import prepare_image, prepare_preview

ROOT = Path(__file__).resolve().parent
DATA = Path(os.environ.get("MOODBOARD_DATA_DIR", ROOT / "data"))
UPLOADS = DATA / "uploads"
THUMBNAILS = DATA / "thumbnails"
AVATARS = DATA / "avatars"
MAX_IMAGE = 10 * 1024 * 1024
# Product rule; Figma sample counters do not override this limit.
BOARD_NAME_LIMIT = 15
_downloads = ThreadPoolExecutor(max_workers=4, thread_name_prefix="pluck-download")


def db():
    return database.connect(DATA / "moodboard.sqlite3")


def store_image(identifier, raw):
    width, height, full, thumbnail = prepare_image(raw)
    with object_store.batch():
        object_store.write_many(DATA, [
            (f"/uploads/{identifier}.png", full),
            (f"/thumbnails/{identifier}.webp", thumbnail),
            (f"/previews/{identifier}.webp", prepare_preview(full)),
        ])
    return width, height, f"/uploads/{identifier}.png", f"/thumbnails/{identifier}.webp"


def seed_library_preview(con, owner):
    if con.execute("SELECT 1 FROM designs WHERE owner_id=? LIMIT 1", (owner,)).fetchone():
        return
    for filename, title in (
        ("interface.webp", "Interface design sample"),
        ("comparison.webp", "Comparison layout sample"),
    ):
        identifier = uuid.uuid4().hex
        raw = (ROOT / "fixtures" / "library" / filename).read_bytes()
        width, height, url, thumbnail = store_image(identifier, raw)
        con.execute(
            "INSERT INTO designs(id,title,image,width,height,url,thumbnail_url,owner_id) VALUES(?,?,?,?,?,?,?,?)",
            (identifier, title, url, width, height, url, thumbnail, owner),
        )


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


def imported_designs(con, identifiers, owner):
    """Return saved cards without making the browser reload the entire library."""
    marks = ",".join("?" for _ in identifiers)
    designs = [dict(row) for row in con.execute(
        f"SELECT * FROM designs WHERE owner_id=? AND id IN ({marks})", (owner, *identifiers)
    )]
    memberships = {}
    for row in con.execute(f"SELECT design_id,board_id FROM board_designs WHERE design_id IN ({marks})", identifiers):
        memberships.setdefault(row["design_id"], []).append(row["board_id"])
    for design in designs:
        design["boards"] = memberships.get(design["id"], [])
    return {"designs": designs, "media_urls": object_store.signed_urls([
        path for design in designs for path in (design["thumbnail_url"], design["creator_avatar_url"], f"/previews/{design['id']}.webp")
    ])}


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
    request = Request(url, headers={
        "User-Agent": "Pluck/0.1", "Accept": "image/*",
    })
    for attempt in range(3):
        try:
            with build_opener(NoRedirect).open(request, timeout=15) as response:
                raw = response.read(MAX_IMAGE + 1)
            break
        except (URLError, TimeoutError, ConnectionError) as error:
            status = getattr(error, "code", None)
            if isinstance(error, HTTPError):
                error.close()
            # Log only a status/type, never credentials or private request data.
            print(f"X image download failed: {status or type(error).__name__} (attempt {attempt + 1}/3)", flush=True)
            transient = status is None or status in (408, 429, 500, 502, 503, 504)
            if transient and attempt < 2:
                time.sleep(0.5 * (attempt + 1))
                continue
            if status in (401, 403):
                message = "X blocked access to an attached image. Please try again later."
            elif status == 404:
                message = "An attached image is no longer available on X."
            elif status == 429:
                message = "X is limiting image downloads. Please try again shortly."
            else:
                message = "Could not download an attached image after retrying. Please try again shortly."
            raise ImportFailure(502, message) from None
    if len(raw) > MAX_IMAGE:
        raise ImportFailure(422, "An attached image exceeds the 10 MB limit.")
    return raw


def fetch_avatar(url, timeout=12):
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
        request = Request(url, headers={"User-Agent": "Pluck/0.1", "Accept": "image/*"})
        for attempt in range(2):
            try:
                with build_opener(NoRedirect).open(request, timeout=timeout) as response:
                    raw = response.read(2 * 1024 * 1024 + 1)
                break
            except (URLError, TimeoutError, ConnectionError) as error:
                status = getattr(error, "code", None)
                if isinstance(error, HTTPError):
                    error.close()
                if attempt == 0 and (status is None or status in (408, 429, 500, 502, 503, 504)):
                    continue
                print(f"X avatar download failed: {status or type(error).__name__}", flush=True)
                return ""
        if len(raw) > 2 * 1024 * 1024:
            return ""
        _, _, _, thumbnail = prepare_image(raw)
        with Image.open(io.BytesIO(thumbnail)) as image:
            image.thumbnail((64, 64))
            identifier = uuid.uuid4().hex
            output = io.BytesIO()
            image.save(output, "WEBP", quality=85)
            object_store.write(DATA, f"/avatars/{identifier}.webp", output.getvalue())
        return f"/avatars/{identifier}.webp"
    except (OSError, ValueError):
        return ""


def download_post_media(photos, avatar):
    """Bound network concurrency while keeping image encoding sequential."""
    photos_pending = [_downloads.submit(fetch_photo, photo["url"]) for photo in photos]
    avatar_pending = _downloads.submit(copy_context().run, fetch_avatar, avatar, timeout=4)
    values, error = [], None
    for future in [*photos_pending, avatar_pending]:
        try:
            values.append(future.result())
        except Exception as failure:
            error = error or failure
    if error:
        raise error
    return values[:-1], values[-1]


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
            subject, email, account = oauth.profile(provider, self, attempt, query.get("code", [""])[0])
            with object_store.batch(), db() as con:
                if provider == "x":
                    account["avatar_url"] = fetch_avatar(account["avatar_url"])
                con.execute("BEGIN IMMEDIATE")
                cookie = oauth.sign_in(con, provider, subject, email, attempt["user_id"], account)
            return self.oauth_redirect("/#library", cookie)
        except ValueError as error:
            message = str(error)
        except (OSError, KeyError, TypeError):
            message = "The provider could not complete sign-in. Please try again."
        return self.oauth_redirect("/?" + urlencode({"auth_error": message}) + "#auth")

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self.respond(200, {"ok": True})
        match = re.fullmatch(r"/api/auth/(google|x)/(start|callback)", path)
        if match:
            return self.oauth_login(*match.groups())
        if path == "/api/auth/me":
            with db() as con:
                user = session_user(self.headers, con)
                cookie = renew_session(self.headers, con) if user else None
            if cookie and os.environ.get("APP_ORIGIN", "").startswith("https://"):
                cookie += "; Secure"
            return self.respond(200, {"user": user}, cookie)
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
                memberships = {}
                for row in con.execute(
                    "SELECT board_designs.design_id,board_designs.board_id FROM board_designs "
                    "JOIN designs ON designs.id=board_designs.design_id WHERE designs.owner_id=?",
                    (user["id"],),
                ):
                    memberships.setdefault(row["design_id"], []).append(row["board_id"])
                for design in designs:
                    design["boards"] = memberships.get(design["id"], [])
            return self.respond(
                200, {"boards": boards, "designs": designs, "user": user,
                      "media_urls": object_store.signed_urls([
                          path for design in designs
                          for path in (design["thumbnail_url"], design["creator_avatar_url"],
                                       f"/previews/{design['id']}.webp")
                      ] + [user.get("avatar_url") if (user.get("avatar_url") or "").startswith("/avatars/") else ""])}
            )
        files = {
            "/": ROOT / "index.html",
            "/index.html": ROOT / "index.html",
            "/app.js": ROOT / "app.js",
            "/styles.css": ROOT / "styles.css",
            "/fonts.css": ROOT / "fonts.css",
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
        preview = re.fullmatch(r"/previews/([a-f0-9]{32})\.webp", path)
        private_media = path.startswith(("/uploads/", "/thumbnails/", "/avatars/", "/previews/"))
        if private_media:
            with db() as con:
                user = session_user(self.headers, con)
                design = None
                if user and preview:
                    design = con.execute(
                        "SELECT url FROM designs WHERE owner_id=? AND id=?",
                        (user["id"], preview[1]),
                    ).fetchone()
                if (
                    not user
                    or not (design if preview else (path == user.get("avatar_url") or con.execute(
                        "SELECT 1 FROM designs WHERE owner_id=? AND (image=? OR url=? OR thumbnail_url=? OR creator_avatar_url=?)",
                        (user["id"], path, path, path, path),
                    ).fetchone()))
                ):
                    return self.respond(404, {"error": "Not found."})
            etag = '"' + path.rsplit("/", 1)[-1] + '"'
            if self.headers.get("If-None-Match") == etag:
                self.send_response(304)
                self.send_header("Cache-Control", "private, max-age=86400, immutable")
                self.send_header("Vary", "Cookie")
                self.send_header("ETag", etag)
                self.end_headers()
                return
        try:
            if preview:
                try:
                    body = object_store.read(DATA, path)
                except FileNotFoundError:
                    body = prepare_preview(object_store.read(DATA, design["url"]))
                    object_store.write(DATA, path, body)
            else:
                body = object_store.read(DATA, path) if private_media else file.read_bytes()
        except (FileNotFoundError, AttributeError, IsADirectoryError):
            return self.respond(404, {"error": "Not found."})
        except OSError:
            return self.respond(502, {"error": "Image storage is unavailable. Try again."})
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
        self.send_response(200)
        self.send_header(
            "Content-Type", types.get(Path(path).suffix if private_media else file.suffix, "application/octet-stream")
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if private_media:
            self.send_header("Cache-Control", "private, max-age=86400, immutable")
            self.send_header("Vary", "Cookie")
            self.send_header("ETag", etag)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.mutate("POST")

    def do_PATCH(self):
        self.mutate("PATCH")

    def do_DELETE(self):
        self.mutate("DELETE")

    def mutate(self, method):
        # APP_ORIGIN is the public HTTPS origin when running behind a proxy.
        origin = self.headers.get("Origin")
        expected_origin = os.environ.get("APP_ORIGIN", f"http://{self.headers.get('Host')}").rstrip("/")
        if origin and origin != expected_origin:
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
            with object_store.batch(), db() as con:
                if path == "/api/auth/continue" and method == "POST":
                    email = clean(body.get("email"), 254).lower()
                    if email.endswith("@oauth.invalid") or not re.fullmatch(
                        r"[^\s@,;<>\x00-\x1f]+@[^\s@,;<>\x00-\x1f]+\.[^\s@,;<>\x00-\x1f]+", email
                    ):
                        raise ValueError("Enter a valid email address.")
                    preview = email_login.send_link(
                        con, email, self.client_address[0], oauth.app_origin(self)
                    )
                    result = {"ok": True}
                    if preview:
                        result["local_preview"] = preview
                    return self.respond(200, result)
                if path == "/api/auth/verify" and method == "POST":
                    token = clean(body.get("token"), 100)
                    user, cookie = email_login.verify(con, token)
                    if os.environ.get("LOCAL_FILLED_LIBRARY") == "1" and email_login.local_preview_enabled(
                        self.client_address[0], oauth.app_origin(self)
                    ):
                        seed_library_preview(con, user["id"])
                    con.commit()
                    if os.environ.get("APP_ORIGIN", "").startswith("https://"):
                        cookie += "; Secure"
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
                    name = clean(body.get("name"), 81)
                    if not name:
                        raise ValueError("Name your board.")
                    if len(name) > BOARD_NAME_LIMIT:
                        raise ValueError(f"Name should not be more than {BOARD_NAME_LIMIT} letters.")
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
                        return self.respond(404, {"error": "Board not found."})
                    if method == "DELETE":
                        removed = [row["id"] for row in con.execute(
                            "SELECT d.id FROM designs d JOIN board_designs bd ON bd.design_id=d.id "
                            "WHERE bd.board_id=? AND d.owner_id=?", (match[1], owner)
                        )]
                        con.executemany("DELETE FROM designs WHERE id=? AND owner_id=?",
                                        [(identifier, owner) for identifier in removed])
                        con.execute("DELETE FROM boards WHERE id=?", (match[1],))
                    else:
                        name = clean(body.get("name"), 81)
                        if not name:
                            raise ValueError("Name your board.")
                        if len(name) > BOARD_NAME_LIMIT:
                            raise ValueError(f"Name should not be more than {BOARD_NAME_LIMIT} letters.")
                        con.execute(
                            "UPDATE boards SET name=? WHERE id=?", (name, match[1])
                        )
                    con.commit()
                    if method == "DELETE":
                        for identifier in removed:
                            for directory, extension in (("uploads", "png"), ("thumbnails", "webp"), ("previews", "webp")):
                                try:
                                    object_store.delete(DATA, f"/{directory}/{identifier}.{extension}")
                                except OSError:
                                    self.log_error("Could not remove deleted board media")
                    return self.respond(200, {"ok": True})
                if path == "/api/import" and method == "POST":
                    board_id = body.get("board_id")
                    if not isinstance(board_id, str) or not con.execute(
                        "SELECT 1 FROM boards WHERE id=? AND owner_id=?", (board_id, owner)
                    ).fetchone():
                        raise ValueError("Select a board!")
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
                        r["id"]
                        for r in con.execute(
                            "SELECT id FROM designs WHERE source=? AND owner_id=?",
                            (source, owner),
                        )
                    ]
                    if existing:
                        for identifier in existing:
                            con.execute("INSERT INTO board_designs(board_id,design_id) VALUES(?,?) "
                                        "ON CONFLICT DO NOTHING", (board_id, identifier))
                        con.commit()
                        return self.respond(200, {"ids": existing, "existing": True, **imported_designs(con, existing, owner)})
                    started = time.perf_counter()
                    title, creator, photos, avatar = fetch_post(match[1], token)
                    post_fetched = time.perf_counter()
                    staged, avatar_url = download_post_media(photos, avatar)
                    downloaded = time.perf_counter()
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
                        con.execute("INSERT INTO board_designs(board_id,design_id) VALUES(?,?)",
                                    (board_id, identifier))
                    con.commit()
                    print(f"X import: images={len(identifiers)} post={post_fetched-started:.2f}s downloads={downloaded-post_fetched:.2f}s processing_uploads={time.perf_counter()-downloaded:.2f}s", flush=True)
                    return self.respond(201, {"ids": identifiers, **imported_designs(con, identifiers, owner)})
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
                            raise ValueError("Choose valid boards.")
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
    if os.environ.get("DATABASE_URL"):
        if not object_store.settings():
            raise ValueError("Hosted storage requires a private Supabase bucket.")
        object_store.verify_bucket()
        with db() as con:
            database.initialize_postgres(con, oauth.SCHEMA + email_login.SCHEMA)
        return
    if object_store.settings():
        raise ValueError("Set DATABASE_URL to persist hosted accounts and boards.")
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
        con.executescript(oauth.SCHEMA + email_login.SCHEMA)
    with db() as con:
        con.execute("BEGIN IMMEDIATE")
        if con.execute("PRAGMA user_version").fetchone()[0] == 0:
            defaults = ["Mobile", "Desktop", "Web", "Branding", "Motion", "Poster"]
            for user in con.execute("SELECT id FROM users"):
                boards = con.execute(
                    "SELECT id,name,rowid FROM boards WHERE owner_id=? ORDER BY rowid LIMIT 6",
                    (user["id"],),
                ).fetchall()
                # Older versions created these six boards together, in this order.
                if [board["name"] for board in boards] == defaults and boards[-1]["rowid"] - boards[0]["rowid"] == 5:
                    con.executemany(
                        "DELETE FROM boards WHERE id=? AND NOT EXISTS "
                        "(SELECT 1 FROM board_designs WHERE board_id=boards.id)",
                        [(board["id"],) for board in boards],
                    )
            con.execute("PRAGMA user_version=1")
    backfill_images()


def load_local_env(path):
    """Read local settings without executing the file as shell code."""
    if not path.is_file():
        return
    allowed = {
        "PORT", "HOST", "APP_ORIGIN", "X_BEARER_TOKEN",
        "DATABASE_URL", "SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_STORAGE_BUCKET", "RESEND_API_KEY", "EMAIL_FROM",
        "LOCAL_EMAIL_PREVIEW", "LOCAL_FILLED_LIBRARY",
        "SMTP_HOST", "SMTP_PORT", "SMTP_FROM", "SMTP_USERNAME", "SMTP_PASSWORD",
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
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"Pluck running at http://{host}:{port}", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
