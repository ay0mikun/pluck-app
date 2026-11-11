import io
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest.mock import Mock, patch

import database
import email_login
import object_store


class HostedTests(unittest.TestCase):
    def test_batch_signed_media_uses_only_requested_paths(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(json.dumps([
            {"path": "thumbnails/a.webp", "signedURL": "/object/sign/pluck-media/thumbnails/a.webp?token=temporary", "error": None},
            {"path": "previews/missing.webp", "signedURL": None, "error": "not found"},
            {"path": "avatars/other.webp", "signedURL": "/object/sign/pluck-media/avatars/other.webp?token=other", "error": None},
        ]).encode())
        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-key", "SUPABASE_STORAGE_BUCKET": "pluck-media",
        }), patch("object_store.build_opener", return_value=opener):
            signed = object_store.signed_urls(["/thumbnails/a.webp", "/previews/missing.webp"])
        self.assertEqual(list(signed), ["/thumbnails/a.webp"])
        self.assertTrue(signed["/thumbnails/a.webp"].startswith("https://project.supabase.co/storage/v1/object/sign/"))
        request = opener.open.call_args.args[0]
        self.assertEqual(json.loads(request.data)["expiresIn"], "3600")
        self.assertEqual(request.full_url, "https://project.supabase.co/storage/v1/object/sign/pluck-media")

    def test_signing_failure_preserves_local_media_fallback(self):
        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://project.supabase.co", "SUPABASE_SERVICE_ROLE_KEY": "test-key",
        }), patch("object_store.build_opener") as opener:
            opener.return_value.open.side_effect = OSError("Offline")
            self.assertEqual(object_store.signed_urls(["/thumbnails/a.webp"]), {})

    def test_postgres_queries_and_write_lock(self):
        connection = Mock()
        hosted = database.Postgres(connection)
        hosted.execute("SELECT email FROM users WHERE id=?", ("account",))
        connection.execute.assert_called_with(
            "SELECT email FROM pluck.users WHERE id=%s", ("account",),
        )
        hosted.execute("BEGIN IMMEDIATE")
        connection.execute.assert_called_with("SELECT pg_advisory_xact_lock(73481902)")

    def test_nested_media_batch_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
            "SUPABASE_URL": "", "SUPABASE_SERVICE_ROLE_KEY": "",
        }):
            with self.assertRaises(ValueError):
                with object_store.batch():
                    object_store.write(directory, "/avatars/author.webp", b"avatar")
                    with object_store.batch():
                        object_store.write(directory, "/uploads/design.png", b"image")
                    raise ValueError("Database failed")
            self.assertFalse((Path(directory) / "avatars/author.webp").exists())
            self.assertFalse((Path(directory) / "uploads/design.png").exists())

    def test_private_storage_request(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b"image")
        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-key",
            "SUPABASE_STORAGE_BUCKET": "pluck-media",
        }), patch("object_store.build_opener", return_value=opener):
            self.assertEqual(object_store.read("/unused", "/uploads/design.png"), b"image")
        request = opener.open.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://project.supabase.co/storage/v1/object/authenticated/pluck-media/uploads/design.png",
        )
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")

    def test_reject_public_bucket(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"public":true}')
        with patch.dict(os.environ, {
            "SUPABASE_URL": "https://project.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-key",
        }), patch("object_store.build_opener", return_value=opener):
            with self.assertRaisesRegex(ValueError, "must be private"):
                object_store.verify_bucket()

    def test_resend_sign_in_link_and_failure(self):
        with closing(sqlite3.connect(":memory:")) as connection:
            connection.row_factory = sqlite3.Row
            connection.executescript(email_login.SCHEMA)
            with patch.dict(os.environ, {
                "RESEND_API_KEY": "test-key", "EMAIL_FROM": "Pluck <hello@example.test>",
                "LOCAL_EMAIL_PREVIEW": "0",
            }), patch("email_login.urlopen") as send:
                send.return_value = io.BytesIO(b'{"id":"email"}')
                email_login.send_link(
                    connection, "tester@example.test", "remote", "https://pluck.example",
                )
                request = send.call_args.args[0]
                payload = json.loads(request.data)
                self.assertEqual(payload["to"], ["tester@example.test"])
                token = payload["text"].split("email-token=")[1].strip()
                stored = connection.execute("SELECT token_hash FROM email_links").fetchone()
                self.assertEqual(stored["token_hash"], email_login.digest(token))
                self.assertEqual(request.full_url, "https://api.resend.com/emails")
                self.assertEqual(request.get_header("User-agent"), "Pluck/0.1")
                send.side_effect = OSError("network failure")
                with self.assertRaisesRegex(ValueError, "couldn’t send"):
                    email_login.send_link(
                        connection, "another@example.test", "remote", "https://pluck.example",
                    )
