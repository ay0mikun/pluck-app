import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image


class API(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.env = {
            **os.environ,
            "PORT": "5199",
            "MOODBOARD_DATA_DIR": cls.temp.name,
            "X_BEARER_TOKEN": "",
        }
        cls.cookie = None
        cls.start()
        req = Request(
            "http://127.0.0.1:5199/api/auth/continue",
            data=json.dumps(
                {"email": "test@example.com", "password": "test-password-123"}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req) as response:
            cls.cookie = response.headers["Set-Cookie"].split(";")[0]

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
            thumb = Image.open(io.BytesIO(res.read()))
            self.assertLessEqual(thumb.width, 720)
            self.assertAlmostEqual(
                thumb.width / thumb.height, item["width"] / item["height"], places=2
            )
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

    def test_unified_auth(self):
        credentials = {"email": "unified@example.test", "password": "a-valid-password"}
        status, created = self.call("api/auth/continue", "POST", credentials)
        self.assertEqual(status, 200)
        status, signed_in = self.call("api/auth/continue", "POST", credentials)
        self.assertEqual(status, 200)
        self.assertEqual(created["user"]["id"], signed_in["user"]["id"])
        self.assertEqual(
            self.call(
                "api/auth/continue",
                "POST",
                {**credentials, "password": "wrong-password"},
            )[0],
            400,
        )
        self.assertEqual(
            self.call("api/auth/continue", "POST", credentials)[1]["user"]["id"],
            created["user"]["id"],
        )

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
        req = Request(
            "http://127.0.0.1:5199/api/auth/continue",
            data=json.dumps(
                {"email": "other@example.com", "password": "another-password"}
            ).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req) as response:
            self.cookie = response.headers["Set-Cookie"].split(";")[0]
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
