"""Shared image validation and upright pixel preparation for inference."""
from io import BytesIO

from fastapi import HTTPException
from PIL import Image as PILImage, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
ALLOWED_FORMATS = {"JPEG": (".jpg", "image/jpeg"), "PNG": (".png", "image/png"), "WEBP": (".webp", "image/webp")}


def prepare_image(blob: bytes) -> tuple[bytes, str, str]:
    try:
        with PILImage.open(BytesIO(blob)) as image:
            fmt = image.format
            if fmt not in ALLOWED_FORMATS:
                raise HTTPException(415, "Only JPEG, PNG and WEBP images are supported")
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise HTTPException(413, "Image must not exceed 25 megapixels")
            image.verify()
        with PILImage.open(BytesIO(blob)) as image:
            upright = ImageOps.exif_transpose(image)
            if fmt == "JPEG":
                upright = upright.convert("RGB")
            upright.info.clear()
            buffer = BytesIO()
            upright.save(buffer, format=fmt)
        normalized = buffer.getvalue()
        if len(normalized) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "Normalized image must not exceed 10 MB; upload a smaller photo")
        extension, media_type = ALLOWED_FORMATS[fmt]
        return normalized, extension, media_type
    except PILImage.DecompressionBombError:
        raise HTTPException(413, "Image dimensions are too large") from None
    except (UnidentifiedImageError, OSError, ValueError):
        raise HTTPException(415, "Upload a valid JPEG, PNG or WEBP image") from None
