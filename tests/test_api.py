import base64
import io
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

from PIL import Image

from email_login import digest


class API(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.env = {
            **os.environ,
            "PORT": "5199",
            "MOODBOARD_DATA_DIR": cls.temp.name,
            "X_BEARER_TOKEN": "",
            "LOCAL_EMAIL_PREVIEW": "1",
            "LOCAL_FILLED_LIBRARY": "0",
            "APP_ORIGIN": "http://127.0.0.1:5199",
            "DATABASE_URL": "",
            "SUPABASE_URL": "",
            "SUPABASE_SERVICE_ROLE_KEY": "",
        }
        cls.cookie = None
        cls.start()
        cls.cookie = cls.sign_in("test@example.com")

    @classmethod
    def email_token(cls, email, lifetime=60):
        token = secrets.token_urlsafe(32)
        now = time.time()
        with closing(sqlite3.connect(Path(cls.temp.name) / "moodboard.sqlite3")) as con, con:
            con.execute(
                "INSERT INTO email_links(token_hash,email,requester,issued,expires) VALUES(?,?,?,?,?)",
                (digest(token), email, "test", now, now + lifetime),
            )
        return token

    @classmethod
    def sign_in(cls, email):
        req = Request(
            "http://127.0.0.1:5199/api/auth/verify",
            data=json.dumps({"token": cls.email_token(email)}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req) as response:
            return response.headers["Set-Cookie"].split(";")[0]

    @classmethod
    def start(cls):
        cls.proc = subprocess.Popen(
            [sys.executable, "server.py"],
            env=cls.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(100):
            try:
                with urlopen("http://127.0.0.1:5199/api/auth/me"):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("Server failed to start")

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait()
        cls.temp.cleanup()

    def call(self, path, method="GET", data=None, origin=None):
        headers = {"Content-Type": "application/json"}
        if self.cookie:
            headers["Cookie"] = self.cookie
        if origin:
            headers["Origin"] = origin
        req = Request(
            "http://127.0.0.1:5199/" + path,
            data=None if data is None else json.dumps(data).encode(),
            headers=headers,
            method=method,
        )
        try:
            with urlopen(req) as res:
                return res.status, json.load(res)
        except HTTPError as e:
            with e:
                return e.code, json.load(e)

    def test_workflow_and_persistence(self):
        _, board = self.call("api/boards", "POST", {"name": "Mobile UI"})
        raw = (Path("assets/background.webp")).read_bytes()
        status, design = self.call(
            "api/designs",
            "POST",
            {
                "title": "Inspiration",
                "image": base64.b64encode(raw).decode(),
                "source": "https://example.com",
                "notes": "Soft palette",
            },
        )
        self.assertEqual(status, 201)
        self.assertEqual(
            self.call(
                "api/designs/" + design["id"], "PATCH", {"boards": [board["id"]]}
            )[0],
            200,
        )
        self.proc.terminate()
        self.proc.wait()
        self.start()
        state = self.call("api/state")[1]
        item = next(d for d in state["designs"] if d["id"] == design["id"])
        self.assertEqual(item["boards"], [board["id"]])
        self.assertGreater(item["width"], 0)
        self.assertGreater(item["height"], 0)
        req = Request(
            "http://127.0.0.1:5199" + item["thumbnail_url"],
            headers={"Cookie": self.cookie},
        )
        with urlopen(req) as res:
            etag = res.headers["ETag"]
            self.assertEqual(res.headers["Vary"], "Cookie")
            self.assertIn("private", res.headers["Cache-Control"])
            thumb = Image.open(io.BytesIO(res.read()))
            self.assertLessEqual(thumb.width, 720)
            self.assertAlmostEqual(
                thumb.width / thumb.height, item["width"] / item["height"], places=2
            )
        cached = Request(req.full_url, headers={"Cookie": self.cookie, "If-None-Match": etag})
        with self.assertRaises(HTTPError) as unchanged:
            urlopen(cached)
        self.assertEqual(unchanged.exception.code, 304)
        unchanged.exception.close()
        # A cached media validator cannot bypass account ownership.
        with self.assertRaises(HTTPError) as unauthorized:
            urlopen(Request(req.full_url, headers={"If-None-Match": etag}))
        self.assertEqual(unauthorized.exception.code, 404)
        unauthorized.exception.close()
        preview_url = "http://127.0.0.1:5199/previews/" + design["id"] + ".webp"
        with urlopen(Request(preview_url, headers={"Cookie": self.cookie})) as res:
            preview = Image.open(io.BytesIO(res.read()))
            self.assertEqual(preview.size, (item["width"], item["height"]))
            self.assertEqual(preview.format, "WEBP")
        with self.assertRaises(HTTPError) as unauthorized_preview:
            urlopen(preview_url)
        self.assertEqual(unauthorized_preview.exception.code, 404)
        unauthorized_preview.exception.close()
        self.call("api/boards/" + board["id"], "PATCH", {"name": "Renamed"})
        self.call("api/boards/" + board["id"], "DELETE", {})
        item = next(
            d for d in self.call("api/state")[1]["designs"] if d["id"] == design["id"]
        )
        self.assertEqual(item["boards"], [])
        self.call("api/designs/" + design["id"], "DELETE", {})
        self.assertFalse(
            any(d["id"] == design["id"] for d in self.call("api/state")[1]["designs"])
        )

    def test_https_staging_origin(self):
        cls = type(self)
        original_origin = cls.env["APP_ORIGIN"]
        cls.proc.terminate()
        cls.proc.wait()
        cls.env["APP_ORIGIN"] = "https://pluck-staging.example"
        try:
            cls.start()
            self.assertEqual(
                self.call("api/boards", "POST", {"name": ""},
                          origin="https://pluck-staging.example")[0],
                400,
            )
            self.assertEqual(
                self.call("api/boards", "POST", {"name": "Blocked"},
                          origin="https://other.example")[0],
                403,
            )
        finally:
            cls.proc.terminate()
            cls.proc.wait()
            cls.env["APP_ORIGIN"] = original_origin
            cls.start()

    def test_passwordless_auth(self):
        email = "unified@example.test"
        status, preview = self.call("api/auth/continue", "POST", {"email": email})
        self.assertEqual(status, 200)
        link = urlsplit(preview["local_preview"])
        self.assertEqual(link.netloc, "127.0.0.1:5199")
        token = parse_qs(link.fragment)["email-token"][0]
        status, created = self.call("api/auth/verify", "POST", {"token": token})
        self.assertEqual(status, 200)
        self.assertEqual(self.call("api/auth/verify", "POST", {"token": token})[0], 400)

        fresh_token = self.email_token(email)
        status, signed_in = self.call("api/auth/verify", "POST", {"token": fresh_token})
        self.assertEqual(status, 200)
        self.assertEqual(created["user"]["id"], signed_in["user"]["id"])

        expired = self.email_token(email, lifetime=-1)
        self.assertEqual(self.call("api/auth/verify", "POST", {"token": expired})[0], 400)
        self.assertEqual(self.call("api/auth/continue", "POST", {"email": "bad"})[0], 400)

    def test_account_isolation(self):
        _, design = self.call(
            "api/designs",
            "POST",
            {
                "title": "Private",
                "image": base64.b64encode(
                    Path("assets/background.webp").read_bytes()
                ).decode(),
            },
        )
        first = self.cookie
        self.cookie = self.sign_in("other@example.com")
        self.assertEqual(self.call("api/state")[1]["designs"], [])
        self.assertEqual(self.call("api/designs/" + design["id"], "DELETE", {})[0], 404)
        self.assertEqual(self.call("uploads/" + design["id"] + ".png")[0], 404)
        self.cookie = None
        self.assertEqual(self.call("api/state")[0], 401)
        self.cookie = first

    def test_validation_and_private_files(self):
        self.assertEqual(self.call("api/boards", "POST", {"name": ""})[0], 400)
        self.assertEqual(
            self.call(
                "api/designs",
                "POST",
                {"title": "Bad", "image": base64.b64encode(b"<svg/>").decode()},
            )[0],
            400,
        )
        self.assertEqual(
            self.call("api/import", "POST", {"url": "https://localhost/status/123"})[0],
            400,
        )
        self.assertEqual(
            self.call(
                "api/boards", "POST", {"name": "Blocked"}, origin="https://evil.example"
            )[0],
            403,
        )
        for path in [
            "server.py",
            "data/moodboard.sqlite3",
            ".env",
            "assets/../server.py",
        ]:
            self.assertEqual(self.call(path)[0], 404)


if __name__ == "__main__":
    unittest.main()
