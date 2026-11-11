import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import oauth
from scripts.build_app import build
from scripts.build_landing import build as build_landing


class DeploymentTests(unittest.TestCase):
    def test_app_build_excludes_landing_and_server_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            build(output, "https://pluck-api.example")
            files = {str(path.relative_to(output)) for path in output.rglob("*") if path.is_file()}
            self.assertFalse(any("landing" in name or ".env" in name or name.endswith(".py") for name in files))
            self.assertNotIn('id="landing"', (output / "static/index.html").read_text())
            self.assertIn('id="auth-screen"', (output / "static/index.html").read_text())
            self.assertIn("static/assets/preview-copy.svg", files)
            self.assertIn("static/assets/geist-sans.woff2", files)
            self.assertIn("static/assets/geist-serif.woff2", files)
            config = json.loads((output / "config.json").read_text())
            self.assertEqual(config["routes"][0]["dest"], "https://pluck-api.example/$1/$2")
            self.assertEqual(config["routes"][0]["headers"]["Cache-Control"], "private, no-store")

    def test_build_requires_a_backend_origin(self):
        for origin in ("", "http://insecure.example", "https://user:secret@example.com", "https://api.example/path", "https://api.example?key=secret"):
            with self.subTest(origin=origin), tempfile.TemporaryDirectory() as directory:
                with self.assertRaises(ValueError):
                    build(Path(directory), origin)

    def test_separate_landing_build_keeps_its_fonts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            build_landing(output)
            self.assertIn('id="landing"', (output / "index.html").read_text())
            self.assertFalse((output / "app.js").exists())
            self.assertTrue((output / "fonts.css").exists())

    def test_oauth_uses_public_origin_through_render_proxy(self):
        handler = SimpleNamespace(headers={"Host": "pluck-api.onrender.com"}, server=SimpleNamespace(server_port=10000))
        with patch.dict(os.environ, {
            "APP_ORIGIN": "https://pluck-app.example",
            "RENDER_EXTERNAL_HOSTNAME": "pluck-api.onrender.com",
        }):
            self.assertEqual(oauth.app_origin(handler), "https://pluck-app.example")
            handler.headers = {"Host": "attacker.example", "X-Forwarded-Host": "pluck-app.example"}
            with self.assertRaises(ValueError):
                oauth.app_origin(handler)
