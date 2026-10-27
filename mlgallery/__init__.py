"""Model Librarian Gallery: your AI images, photos and illustrations, and (for the AI ones) what made them."""

VERSION = "1.18.0"
APP_NAME = "Model Librarian Gallery"

try:  # iPhone photos (HEIC/HEIF) open through Pillow like any other picture, when pillow-heif is installed
    from pillow_heif import register_heif_opener as _heif

    _heif()
except Exception:
    pass
