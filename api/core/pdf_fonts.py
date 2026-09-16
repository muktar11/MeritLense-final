import base64
import functools
from pathlib import Path

# Reuses the same bundled Noto Naskh Arabic font files already shipped for
# api/contracts' Arabic PDF templates (B2B/B2C agreements, DPA) - one set of
# font assets for every Arabic PDF in the app, not duplicated per-app.
_FONTS_DIR = Path(__file__).resolve().parent.parent / "contracts" / "static_fonts"


@functools.lru_cache(maxsize=None)
def arabic_font_data_uri(filename):
    """Base64 data URI for a bundled Noto Naskh Arabic weight, embedded
    directly in rendered HTML so WeasyPrint (and a browser preview) render
    Arabic glyphs correctly without depending on static file serving."""
    encoded = base64.b64encode((_FONTS_DIR / filename).read_bytes()).decode()
    return f"data:font/ttf;base64,{encoded}"


def arabic_font_context():
    """The two font-weight data URIs a `{% if language == "ar" %}` block in
    a template needs for its @font-face declarations."""
    return {
        "arabic_font_regular_uri": arabic_font_data_uri("NotoNaskhArabic-Regular.ttf"),
        "arabic_font_bold_uri": arabic_font_data_uri("NotoNaskhArabic-Bold.ttf"),
    }
