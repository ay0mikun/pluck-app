"""Account creation and session storage."""

import hashlib
import secrets
import time
from http.cookies import SimpleCookie


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


def create_user(con, email):
    first_user = not con.execute("SELECT 1 FROM users").fetchone()
    identifier = secrets.token_hex(16)
    con.execute(
        "INSERT INTO users VALUES(?,?,?,?)",
        (identifier, email, "", ""),
    )
    if first_user:
        con.execute(
            "UPDATE designs SET owner_id=? WHERE owner_id IS NULL", (identifier,)
        )
        con.execute(
            "UPDATE boards SET owner_id=? WHERE owner_id IS NULL", (identifier,)
        )
    return identifier


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
