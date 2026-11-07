"""Decode images once and create grid-sized previews without changing aspect ratio."""

import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

Image.MAX_IMAGE_PIXELS = 40_000_000


def prepare_image(raw):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ("JPEG", "PNG", "GIF", "WEBP"):
                    raise ValueError("Use a PNG, JPEG, GIF, or WebP image.")
                image.seek(0)
                oriented = ImageOps.exif_transpose(image)
                width, height = oriented.size
                if width < 1 or height < 1:
                    raise ValueError("This image has invalid dimensions.")
                oriented.load()
                # Preserve transparency and bake EXIF orientation into the full image.
                full = io.BytesIO()
                oriented.save(full, "PNG")
                thumbnail = oriented.convert(
                    "RGBA" if "A" in oriented.getbands() else "RGB"
                )
                thumbnail.thumbnail((640, 1600), Image.Resampling.LANCZOS)
                preview = io.BytesIO()
                thumbnail.save(preview, "WEBP", quality=80, method=4)
                return width, height, full.getvalue(), preview.getvalue()
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
    ):
        raise ValueError("This image is invalid or too large to process.")
