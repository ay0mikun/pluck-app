import io
import json
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

import server


class ImportTests(unittest.TestCase):
    def opener(self, payload):
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps(payload).encode())
        return opener

    def test_public_import_metadata(self):
        opener = self.opener(
            {
                "code": 200,
                "tweet": {
                    "text": "A UI study",
                    "author": {
                        "screen_name": "designer",
                        "avatar_url": "https://pbs.twimg.com/profile_images/example.jpg",
                    },
                    "media": {
                        "photos": [{"url": "https://pbs.twimg.com/media/example.jpg"}]
                    },
                },
            }
        )
        with patch("server.build_opener", return_value=opener):
            title, creator, photos, avatar = server.fetch_post("123")
        self.assertEqual((title, creator), ("A UI study", "designer"))
        self.assertEqual(len(photos), 1)
        self.assertEqual(avatar, "https://pbs.twimg.com/profile_images/example.jpg")
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.fxtwitter.com/status/123")
        self.assertFalse(request.has_header("Authorization"))

    def test_official_import_metadata(self):
        opener = self.opener(
            {
                "data": {"text": "Official", "author_id": "42"},
                "includes": {
                    "users": [{"id": "42", "username": "creator"}],
                    "media": [
                        {
                            "type": "photo",
                            "url": "https://pbs.twimg.com/media/example.jpg",
                        }
                    ],
                },
            }
        )
        with patch("server.build_opener", return_value=opener):
            self.assertEqual(
                server.fetch_post("123", "test-token")[:2], ("Official", "creator")
            )
        self.assertEqual(
            opener.open.call_args.args[0].get_header("Authorization"),
            "Bearer test-token",
        )

    def test_no_photos_and_unavailable(self):
        for payload in [
            {"code": 404, "tweet": None},
            {"code": 200, "tweet": {"text": "Text only"}},
        ]:
            with patch("server.build_opener", return_value=self.opener(payload)):
                with self.assertRaises(server.ImportFailure) as caught:
                    server.fetch_post("123")
                self.assertEqual(caught.exception.status, 422)

    def test_provider_rate_limit(self):
        opener = Mock()
        opener.open.side_effect = HTTPError(
            "https://api.fxtwitter.com", 429, "Busy", {}, None
        )
        with patch("server.build_opener", return_value=opener):
            with self.assertRaises(server.ImportFailure) as caught:
                server.fetch_post("123")
        self.assertEqual(caught.exception.status, 429)

    def test_media_allowlist_and_size(self):
        for url in [
            "http://pbs.twimg.com/media/a.png",
            "https://localhost/media/a.png",
            "https://pbs.twimg.com.evil.test/media/a.png",
            "https://pbs.twimg.com:444/media/a.png",
        ]:
            with patch("server.build_opener") as opener:
                with self.assertRaises(server.ImportFailure):
                    server.fetch_photo(url)
                opener.assert_not_called()
        opener = Mock()
        opener.open.return_value = io.BytesIO(b"x" * (server.MAX_IMAGE + 1))
        with patch("server.build_opener", return_value=opener):
            with self.assertRaises(server.ImportFailure) as caught:
                server.fetch_photo("https://pbs.twimg.com/media/a.png")
        self.assertEqual(caught.exception.status, 422)
