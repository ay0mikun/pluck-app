import threading
import unittest
from unittest.mock import patch

import object_store
import server


class ImportPerformanceTests(unittest.TestCase):
    def test_downloads_overlap_and_preserve_photo_order(self):
        ready = threading.Barrier(4)
        def photo(url):
            ready.wait(timeout=3)
            return url.encode()
        with patch('server.fetch_photo', side_effect=photo), patch('server.fetch_avatar', return_value='avatar') as avatar:
            photos, profile = server.download_post_media([{'url': str(i)} for i in range(4)], 'profile')
        self.assertEqual(photos, [b'0', b'1', b'2', b'3'])
        self.assertEqual(profile, 'avatar')
        avatar.assert_called_once_with('profile', timeout=4)

    def test_parallel_upload_failure_drains_workers_before_rollback(self):
        ready = threading.Barrier(3)
        completed, deleted = [], []
        lock = threading.Lock()
        def request(method, path, raw=None):
            if method == 'POST':
                ready.wait(timeout=3)
                with lock:
                    completed.append(path)
                if path == '/bad.webp':
                    raise OSError('storage failure')
            else:
                self.assertEqual(len(completed), 3)
                deleted.append(path)
        files = [(f'/{name}.webp', b'image') for name in ('one', 'bad', 'three')]
        with patch('object_store.settings', return_value=('url', 'key', 'bucket')), patch('object_store.request', side_effect=request):
            with self.assertRaises(OSError), object_store.batch():
                object_store.write_many('/unused', files)
        self.assertEqual(set(deleted), {path for path, _ in files})
