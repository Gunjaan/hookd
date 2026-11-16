"""Image conversions for the post studio (Pillow)."""

import io

from PIL import Image, ImageOps, UnidentifiedImageError

from apps.creatives.exceptions import CreativeEngineError

SOURCE_MAX_SIDE = 1024
SOURCE_QUALITY = 85
POST_SIZE = (1080, 1350)
POST_QUALITY = 90


def _open(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    image = ImageOps.exif_transpose(image)
    return image.convert("RGB")


def _jpeg(image: Image.Image, quality: int) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=quality, optimize=True)
    return buffer.getvalue()


def source_jpeg(data: bytes) -> bytes:
    """Product photo for the AI: upright, at most 1024 px on the long side. Keeps input tokens low."""
    image = _open(data)
    image.thumbnail((SOURCE_MAX_SIDE, SOURCE_MAX_SIDE), Image.Resampling.LANCZOS)
    return _jpeg(image, SOURCE_QUALITY)


def post_jpeg(data: bytes) -> bytes:
    """Generated image as an Instagram ready 1080x1350 (4:5) JPEG, center cropped if the ratio is off."""
    try:
        image = _open(data)
    except (UnidentifiedImageError, OSError) as exc:
        raise CreativeEngineError("The AI returned a broken photo. Please try again.") from exc
    return _jpeg(ImageOps.fit(image, POST_SIZE, Image.Resampling.LANCZOS), POST_QUALITY)
