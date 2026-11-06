import io
import unittest

from PIL import Image

from media import prepare_image


class MediaTests(unittest.TestCase):
    def test_dimensions_thumbnails_and_extreme_ratios(self):
        for width, height in [(1600, 900), (900, 1600), (100, 4000), (4000, 100)]:
            with self.subTest(size=(width, height)):
                source = io.BytesIO()
                Image.new("RGB", (width, height), "#e6e8dd").save(source, "PNG")
                w, h, full, thumbnail = prepare_image(source.getvalue())
                self.assertEqual((w, h), (width, height))
                self.assertEqual(Image.open(io.BytesIO(full)).size, (width, height))
                preview = Image.open(io.BytesIO(thumbnail))
                self.assertLessEqual(preview.width, 720)
                self.assertLessEqual(preview.height, 1800)
                self.assertAlmostEqual(
                    preview.width / preview.height, w / h, delta=0.04
                )

    def test_exif_orientation(self):
        image = Image.new("RGB", (120, 80), "white")
        exif = image.getexif()
        exif[274] = 6
        raw = io.BytesIO()
        image.save(raw, "JPEG", exif=exif)
        self.assertEqual(prepare_image(raw.getvalue())[:2], (80, 120))

    def test_rejects_invalid_images(self):
        for raw in (b"<svg/>", b"\x89PNG\r\n\x1a\nnot-really-an-image"):
            with self.assertRaises(ValueError):
                prepare_image(raw)
