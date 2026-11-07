"""Private media storage, local in development or in a Supabase bucket."""

import os
import json
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

_pending = ContextVar("pending_media", default=None)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def settings():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not url and not key:
        return None
    parsed = urlsplit(url)
    if (not key or parsed.scheme != "https" or not parsed.netloc or
            parsed.path or parsed.query or parsed.fragment or parsed.username):
        raise ValueError("Configure SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.")
    bucket = os.environ.get("SUPABASE_STORAGE_BUCKET", "pluck-media")
    return url, key, quote(bucket, safe="")


def request(method, path, raw=None):
    url, key, bucket = settings()
    object_path = quote(path.lstrip("/"), safe="/")
    route = "object/authenticated" if method == "GET" else "object"
    endpoint = f"{url}/storage/v1/{route}/{bucket}/{object_path}"
    headers = {"apikey": key, "Authorization": f"Bearer {key}"}
    if raw is not None:
        headers["Content-Type"] = "image/png" if path.endswith(".png") else "image/webp"
    try:
        with build_opener(NoRedirect).open(
            Request(endpoint, data=raw, headers=headers, method=method), timeout=30,
        ) as response:
            return response.read()
    except HTTPError as error:
        detail = error.read(4096).decode(errors="replace")
        missing = error.code == 404 or (
            error.code == 400 and "not found" in detail.lower()
        )
        if method in ("GET", "DELETE") and missing:
            if method == "GET":
                raise FileNotFoundError(path) from None
            return b""
        raise OSError("Image storage is unavailable. Please try again.") from None


def verify_bucket():
    url, key, bucket = settings()
    request = Request(
        f"{url}/storage/v1/bucket/{bucket}",
        headers={"apikey": key, "Authorization": f"Bearer {key}"},
    )
    try:
        with build_opener(NoRedirect).open(request, timeout=15) as response:
            bucket_info = json.load(response)
    except (OSError, ValueError):
        raise ValueError("Create the private Supabase storage bucket before starting.") from None
    if bucket_info.get("public") is not False:
        raise ValueError("The Supabase storage bucket must be private.")


def signed_urls(paths):
    """Sign only media already authorized by the caller; keep the bucket private."""
    configured = settings()
    paths = sorted({path.lstrip("/") for path in paths if path})
    if not configured or not paths:
        return {}
    url, key, bucket = configured
    result = {}
    try:
        for offset in range(0, len(paths), 200):
            selected = paths[offset:offset + 200]
            request = Request(
                f"{url}/storage/v1/object/sign/{bucket}",
                data=json.dumps({"paths": selected, "expiresIn": "3600"}).encode(),
                headers={"apikey": key, "Authorization": f"Bearer {key}",
                         "Content-Type": "application/json", "User-Agent": "Pluck/0.1"},
                method="POST",
            )
            with build_opener(NoRedirect).open(request, timeout=8) as response:
                items = json.loads(response.read(2 * 1024 * 1024))
            for item in items:
                path, signed = item.get("path"), item.get("signedURL")
                if path in selected and not item.get("error") and isinstance(signed, str) and signed.startswith("/object/sign/"):
                    result["/" + path] = url + "/storage/v1" + signed
    except (OSError, ValueError, TypeError, AttributeError):
        # Existing owner-checked media routes remain a fallback.
        pass
    return result


def write(data_directory, path, raw):
    pending = _pending.get()
    if pending is not None:
        pending.append((data_directory, path))
    if settings():
        request("POST", path, raw)
    else:
        file = Path(data_directory) / path.lstrip("/")
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(raw)


def read(data_directory, path):
    if settings():
        return request("GET", path)
    return (Path(data_directory) / path.lstrip("/")).read_bytes()


def delete(data_directory, path):
    if settings():
        request("DELETE", path)
    else:
        (Path(data_directory) / path.lstrip("/")).unlink(missing_ok=True)


@contextmanager
def batch():
    if _pending.get() is not None:
        yield
        return
    pending = []
    token = _pending.set(pending)
    try:
        yield
    except Exception:
        for directory, path in pending:
            try:
                delete(directory, path)
            except OSError:
                pass
        raise
    finally:
        _pending.reset(token)
