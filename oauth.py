"""Authorization-code login with browser-bound state and PKCE."""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from http.cookies import SimpleCookie
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from auth import create_user, issue_session, session_user

PROVIDERS = {
    "google": (
        "https://accounts.google.com/o/oauth2/v2/auth",
        "https://oauth2.googleapis.com/token",
        "https://openidconnect.googleapis.com/v1/userinfo",
        "openid email profile",
    ),
    "x": (
        "https://x.com/i/oauth2/authorize",
        "https://api.x.com/2/oauth2/token",
        "https://api.x.com/2/users/me",
        "tweet.read users.read",
    ),
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_attempts (
    state TEXT PRIMARY KEY, provider TEXT NOT NULL, verifier TEXT NOT NULL,
    callback TEXT NOT NULL, user_id TEXT, expires INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_identities (
    provider TEXT NOT NULL, subject TEXT NOT NULL,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    PRIMARY KEY (provider, subject)
);
"""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def settings(provider, handler):
    prefix = provider.upper() + "_OAUTH_"
    client_id = os.environ.get(prefix + "CLIENT_ID")
    secret = os.environ.get(prefix + "CLIENT_SECRET")
    if not client_id or not secret:
        raise ValueError("This provider needs OAuth credentials on the server.")
    port = handler.server.server_port
    host = handler.headers.get("Host")
    origin = os.environ.get("APP_ORIGIN")
    if not origin and host in (f"localhost:{port}", f"127.0.0.1:{port}"):
        origin = "http://" + host
    parsed = urlsplit(origin or "")
    if parsed.netloc != host or not (
        parsed.scheme == "https"
        or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"))
    ) or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Open the app using its configured OAuth origin.")
    return client_id, secret, origin.rstrip("/") + f"/api/auth/{provider}/callback"


def start(con, provider, handler):
    client_id, _, callback = settings(provider, handler)
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    user = session_user(handler.headers, con)
    con.execute("DELETE FROM oauth_attempts WHERE expires <= ?", (time.time(),))
    con.execute(
        "INSERT INTO oauth_attempts VALUES(?,?,?,?,?,?)",
        (state, provider, verifier, callback, user["id"] if user else None, time.time() + 600),
    )
    authorize, _, _, scope = PROVIDERS[provider]
    params = dict(client_id=client_id, redirect_uri=callback, response_type="code",
                  scope=scope, state=state, code_challenge=challenge, code_challenge_method="S256")
    cookie = f"moodboard_oauth={state}; HttpOnly; SameSite=Lax; Path=/api/auth/; Max-Age=600"
    return authorize + "?" + urlencode(params), cookie


def consume(con, provider, handler, query):
    state = query.get("state", [""])[0]
    cookies = SimpleCookie(handler.headers.get("Cookie", ""))
    browser_state = cookies.get("moodboard_oauth")
    if not state or not browser_state or not hmac.compare_digest(state, browser_state.value):
        raise ValueError("This sign-in attempt expired. Please try again.")
    attempt = con.execute(
        "DELETE FROM oauth_attempts WHERE state=? AND provider=? AND expires>? RETURNING *",
        (state, provider, time.time()),
    ).fetchone()
    user = session_user(handler.headers, con)
    if not attempt or attempt["user_id"] != (user["id"] if user else None):
        raise ValueError("This sign-in attempt expired. Please try again.")
    return attempt


def request_json(request):
    with build_opener(NoRedirect).open(request, timeout=15) as response:
        return json.loads(response.read(256 * 1024 + 1))


def profile(provider, handler, attempt, code):
    client_id, secret, callback = settings(provider, handler)
    if not code or callback != attempt["callback"]:
        raise ValueError("Sign-in was cancelled or its callback changed.")
    _, token_url, profile_url, _ = PROVIDERS[provider]
    params = dict(grant_type="authorization_code", code=code, redirect_uri=callback,
                  code_verifier=attempt["verifier"], client_id=client_id)
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if provider == "x":
        credentials = base64.b64encode(f"{client_id}:{secret}".encode()).decode()
        headers["Authorization"] = "Basic " + credentials
    else:
        params["client_secret"] = secret
    token = request_json(Request(token_url, urlencode(params).encode(), headers))
    details = request_json(Request(profile_url, headers={"Authorization": "Bearer " + token["access_token"]}))
    if provider == "google":
        subject = details["sub"]
        email = details.get("email", "").lower() if details.get("email_verified") is True else ""
    else:
        subject, email = details["data"]["id"], ""
    if not isinstance(subject, str) or not subject:
        raise ValueError("The provider did not return an account identifier.")
    return subject, email


def sign_in(con, provider, subject, email, link_user):
    identity = con.execute(
        "SELECT user_id FROM oauth_identities WHERE provider=? AND subject=?", (provider, subject)
    ).fetchone()
    if identity:
        identifier = identity["user_id"]
        if link_user and identifier != link_user:
            raise ValueError("This provider account is already linked to another library.")
    else:
        if link_user:
            identifier = link_user
        else:
            if email and con.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
                raise ValueError("Sign in with your existing password, then connect Google from the account menu.")
            placeholder = hashlib.sha256(f"{provider}:{subject}".encode()).hexdigest()
            identifier = create_user(con, email or f"{placeholder}@oauth.invalid")
        con.execute("INSERT INTO oauth_identities VALUES(?,?,?)", (provider, subject, identifier))
    return issue_session(con, identifier)
