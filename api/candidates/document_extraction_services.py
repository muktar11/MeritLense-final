import base64
import json
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = (
    "You are looking at a photo or scan of an identity document (passport, "
    "national ID, driver's license, etc). Extract exactly these three fields "
    "if they are clearly legible: the holder's first/given name, their "
    "last/family name, and the document's passport or ID number. Respond "
    "with ONLY a JSON object with keys \"first_name\", \"last_name\", "
    "\"passport_id\" - use null for any field that isn't clearly visible or "
    "legible. Never guess or invent a value."
)

EMPTY_RESULT = {"first_name": None, "last_name": None, "passport_id": None}


def _rewind(uploaded_file):
    if hasattr(uploaded_file, "seek"):
        try:
            uploaded_file.seek(0)
        except Exception:
            pass


def _read_bytes(uploaded_file):
    _rewind(uploaded_file)
    if hasattr(uploaded_file, "chunks"):
        data = b"".join(chunk for chunk in uploaded_file.chunks())
    else:
        data = uploaded_file.read()
    _rewind(uploaded_file)
    return data


def _pdf_first_page_to_png_bytes(pdf_bytes):
    """Rasterizes page 1 of a PDF to PNG bytes via pdftoppm (poppler-utils) -
    same approach as InterviewSessionPrecheckService._convert_pdf_reference_to_image
    (api/sessions/identity_services.py), kept as a small self-contained copy
    here rather than importing across apps for one shared utility."""
    pdftoppm_path = shutil.which("pdftoppm")
    if not pdftoppm_path:
        logger.warning("Passport field extraction: pdftoppm not installed, skipping PDF document")
        return None

    with tempfile.TemporaryDirectory(prefix="candidate-doc-") as tmpdir:
        source_path = Path(tmpdir) / "document.pdf"
        output_prefix = str(Path(tmpdir) / "document-page")
        source_path.write_bytes(pdf_bytes)
        try:
            subprocess.run(
                [pdftoppm_path, "-f", "1", "-l", "1", "-singlefile", "-png", str(source_path), output_prefix],
                check=True, capture_output=True, text=True,
            )
        except subprocess.CalledProcessError:
            logger.exception("Failed to rasterize passport PDF for field extraction")
            return None

        image_path = Path(f"{output_prefix}.png")
        if not image_path.exists():
            return None
        return image_path.read_bytes()


def _normalize(value):
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def extract_passport_fields(uploaded_file):
    """Best-effort extraction of first_name/last_name/passport_id from an
    uploaded passport/ID document image or PDF, via the same OpenAI vision
    model already configured for interview response interpretation
    (OPENAI_API_KEY / OPENAI_INTERPRETATION_MODEL - gpt-4o-mini by default,
    vision-capable). Never raises - any failure (missing config, bad image,
    API error, unparsable response) just returns EMPTY_RESULT, since this
    is a convenience pre-fill the user can always complete by hand, not a
    required step for candidate creation."""
    api_key = settings.OPENAI_API_KEY
    api_url = settings.OPENAI_INTERPRETATION_API_URL
    model = settings.OPENAI_INTERPRETATION_MODEL
    if not (api_key and api_url and model):
        return dict(EMPTY_RESULT)

    content_type = (getattr(uploaded_file, "content_type", "") or "").lower()
    name = (getattr(uploaded_file, "name", "") or "").lower()
    is_pdf = content_type == "application/pdf" or name.endswith(".pdf")

    try:
        raw_bytes = _read_bytes(uploaded_file)
        if is_pdf:
            image_bytes = _pdf_first_page_to_png_bytes(raw_bytes)
            mime = "image/png"
        else:
            image_bytes = raw_bytes
            mime = content_type if content_type.startswith("image/") else "image/jpeg"

        if not image_bytes:
            return dict(EMPTY_RESULT)

        data_uri = f"data:{mime};base64,{base64.b64encode(image_bytes).decode()}"
        payload = {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": "You extract structured identity-document fields only and respond with valid JSON.",
                },
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": EXTRACTION_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                },
            ],
        }
        response = requests.post(
            api_url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=settings.AI_INTERPRETATION_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        choices = body.get("choices") or []
        if not choices:
            return dict(EMPTY_RESULT)

        content_str = (choices[0].get("message") or {}).get("content") or "{}"
        parsed = json.loads(content_str)
        if not isinstance(parsed, dict):
            return dict(EMPTY_RESULT)

        return {
            "first_name": _normalize(parsed.get("first_name")),
            "last_name": _normalize(parsed.get("last_name")),
            "passport_id": _normalize(parsed.get("passport_id")),
        }
    except Exception:
        logger.exception("Passport field extraction failed")
        return dict(EMPTY_RESULT)
