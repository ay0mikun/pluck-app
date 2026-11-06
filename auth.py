"""Local email/password sessions. OAuth credentials are deliberately not simulated."""

import hashlib
import hmac
import secrets
import time
from http.cookies import SimpleCookie


def password_hash(password, salt):
    return hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1
    ).hex()


def session_user(headers, con):
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("Cookie", ""))
    except Exception:
        return None
    value = cookie.get("moodboard_session")
    if not value:
        return None
    key = hashlib.sha256(value.value.encode()).hexdigest()
    row = con.execute(
        "SELECT users.id,users.email FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token=? AND sessions.expires>?",
        (key, time.time()),
    ).fetchone()
    return dict(row) if row else None


def create_user(con, email, password=None):
    first_user = not con.execute("SELECT 1 FROM users").fetchone()
    identifier = secrets.token_hex(16)
    salt = secrets.token_hex(16)
    con.execute(
        "INSERT INTO users VALUES(?,?,?,?)",
        (identifier, email, salt, password_hash(password, salt) if password is not None else ""),
    )
    if first_user:
        con.execute(
            "UPDATE designs SET owner_id=? WHERE owner_id IS NULL", (identifier,)
        )
        con.execute(
            "UPDATE boards SET owner_id=? WHERE owner_id IS NULL", (identifier,)
        )
    for name in ("Mobile", "Desktop", "Web", "Branding", "Motion", "Poster"):
        if not con.execute(
            "SELECT 1 FROM boards WHERE owner_id=? AND lower(name) LIKE ?",
            (identifier, name.lower() + "%"),
        ).fetchone():
            con.execute(
                "INSERT INTO boards(id,name,owner_id) VALUES(?,?,?)",
                (secrets.token_hex(16), name, identifier),
            )
    return identifier


def authenticate(con, email, password, register=False):
    if register:
        identifier = create_user(con, email, password)
    else:
        row = con.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not row or not row["password_hash"] or not hmac.compare_digest(
            row["password_hash"], password_hash(password, row["salt"])
        ):
            raise ValueError("The email or password is incorrect.")
        identifier = row["id"]
    return {"id": identifier, "email": email}, issue_session(con, identifier)


def issue_session(con, identifier):
    token = secrets.token_urlsafe(32)
    con.execute("DELETE FROM sessions WHERE expires<=?", (time.time(),))
    con.execute(
        "INSERT INTO sessions VALUES(?,?,?)",
        (
            hashlib.sha256(token.encode()).hexdigest(),
            identifier,
            time.time() + 7 * 86400,
        ),
    )
    return f"moodboard_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=604800"
