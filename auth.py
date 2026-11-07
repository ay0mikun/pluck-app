"""Account creation and session storage."""

import hashlib
import secrets
import time
from http.cookies import SimpleCookie

SESSION_AGE = 90 * 86400


def session_token(headers):
    cookie = SimpleCookie()
    try:
        cookie.load(headers.get("Cookie", ""))
    except Exception:
        return None
    value = cookie.get("moodboard_session")
    if not value:
        return None
    return value.value


def session_user(headers, con):
    token = session_token(headers)
    if not token:
        return None
    key = hashlib.sha256(token.encode()).hexdigest()
    row = con.execute(
        "SELECT users.id,users.email,user_profiles.display_name,user_profiles.username,user_profiles.avatar_url "
        "FROM sessions JOIN users ON users.id=sessions.user_id "
        "LEFT JOIN user_profiles ON user_profiles.user_id=users.id WHERE sessions.token=? AND sessions.expires>?",
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
            time.time() + SESSION_AGE,
        ),
    )
    return session_cookie(token)


def session_cookie(token):
    return f"moodboard_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_AGE}"


def renew_session(headers, con):
    token = session_token(headers)
    if not token:
        return None
    row = con.execute(
        "UPDATE sessions SET expires=? WHERE token=? AND expires>? RETURNING user_id",
        (time.time() + SESSION_AGE, hashlib.sha256(token.encode()).hexdigest(), time.time()),
    ).fetchone()
    return session_cookie(token) if row else None
