"""Single-use email sign-in links."""

import hashlib
import os
import secrets
import smtplib
import ssl
import time
from email.message import EmailMessage
from urllib.parse import urlsplit

from auth import create_user, issue_session

SCHEMA = """
CREATE TABLE IF NOT EXISTS email_links(
    token_hash TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    requester TEXT NOT NULL,
    issued REAL NOT NULL,
    expires REAL NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0
);
"""


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def local_preview_enabled(requester, origin):
    return (
        os.environ.get("LOCAL_EMAIL_PREVIEW") == "1"
        and requester == "127.0.0.1"
        and urlsplit(origin).hostname in ("127.0.0.1", "localhost")
    )


def send_link(con, email, requester, origin):
    host = os.environ.get("SMTP_HOST")
    sender = os.environ.get("SMTP_FROM")
    local_preview = local_preview_enabled(requester, origin)
    if not local_preview and (not host or not sender):
        raise ValueError("Email sign-in is unavailable. Continue with X.")

    now = time.time()
    con.execute("BEGIN IMMEDIATE")
    con.execute("DELETE FROM email_links WHERE expires <= ?", (now,))
    recent = con.execute(
        "SELECT count(*) FROM email_links WHERE issued > ? AND (email = ? OR requester = ?)",
        (now - 60, email, requester),
    ).fetchone()[0]
    if recent >= 3:
        raise ValueError("Too many requests. Try again in a minute.")
    token = secrets.token_urlsafe(32)
    token_hash = digest(token)
    con.execute(
        "INSERT INTO email_links(token_hash,email,requester,issued,expires) VALUES(?,?,?,?,?)",
        (token_hash, email, requester, now, now + 900),
    )
    con.commit()

    link = f"{origin}/#email-token={token}"
    if local_preview:
        return link

    message = EmailMessage()
    message["Subject"] = "Sign in to Pluck"
    message["From"] = sender
    message["To"] = email
    message.set_content(f"Open this link within 15 minutes to sign in to Pluck:\n\n{link}\n")
    try:
        with smtplib.SMTP_SSL(host, int(os.environ.get("SMTP_PORT", "465")),
                              timeout=10, context=ssl.create_default_context()) as smtp:
            username = os.environ.get("SMTP_USERNAME")
            if username:
                smtp.login(username, os.environ.get("SMTP_PASSWORD", ""))
            smtp.send_message(message)
    except (OSError, ValueError):
        raise ValueError("We couldn’t send the email. Try again or continue with X.") from None


def verify(con, token):
    con.execute("BEGIN IMMEDIATE")
    row = con.execute(
        "SELECT email FROM email_links WHERE token_hash=? AND expires>? AND consumed=0",
        (digest(token), time.time()),
    ).fetchone()
    if not row:
        raise ValueError("This sign-in link has expired or already been used. Request another.")
    email = row["email"]
    con.execute("UPDATE email_links SET consumed=1 WHERE token_hash=?", (digest(token),))
    user = con.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    identifier = user["id"] if user else create_user(con, email)
    return {"id": identifier, "email": email}, issue_session(con, identifier)
