import base64
import hashlib
import io
import secrets

from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string
from django.utils import timezone

from api.core.constants import AgreementType, Nationalities

TEMPLATE_BY_TYPE = {
    AgreementType.B2B_AGREEMENT: "contracts/b2b_agreement.html",
    AgreementType.DPA: "contracts/dpa.html",
    AgreementType.B2C_AGREEMENT: "contracts/b2c_agreement.html",
}

# Company stamp only applies to the B2B-scoped documents — B2C signers have
# no company, so B2C_AGREEMENT is intentionally excluded here.
STAMPED_TYPES = {AgreementType.B2B_AGREEMENT, AgreementType.DPA}


def _individual_context(user):
    """Context fields for B2C_AGREEMENT, sourced from the signer's own
    account rather than a Company (individual employers have none).
    """
    if user is None:
        return {"individual_name": "", "individual_email": "", "individual_nationality": ""}

    profile = getattr(user, "individual_profile", None)
    nationality_code = getattr(profile, "nationality", "") if profile else ""
    return {
        "individual_name": user.get_full_name() if hasattr(user, "get_full_name") else "",
        "individual_email": getattr(user, "email", ""),
        "individual_nationality": dict(Nationalities.CHOICES).get(nationality_code, nationality_code),
    }


def generate_contract_id():
    year = timezone.now().year
    suffix = secrets.token_hex(3).upper()
    return f"ML-AGR-{year}-{suffix}"


def _build_qr_data_uri(verification_url):
    import qrcode

    img = qrcode.make(verification_url)
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode()
    return f"data:image/png;base64,{encoded}"


def compute_pdf_hash(file_field):
    """SHA-256 hex digest of a saved FileField's binary content. Computed
    from the already-rendered PDF (post-stamp), so it cannot itself be
    printed inside that same PDF without becoming self-referential — it's
    stored on the record and served via the download/audit/verify
    endpoints instead, matching how the public QR verification portal
    displays it for offline comparison against a downloaded copy.
    """
    file_field.open("rb")
    try:
        digest = hashlib.sha256()
        for chunk in file_field.chunks():
            digest.update(chunk)
        return digest.hexdigest()
    finally:
        file_field.close()


def _file_to_data_uri(file_field):
    if not file_field:
        return None
    try:
        file_field.open("rb")
        content = file_field.read()
    finally:
        file_field.close()
    mime = "image/png" if file_field.name.lower().endswith("png") else "image/jpeg"
    encoded = base64.b64encode(content).decode()
    return f"data:{mime};base64,{encoded}"


def render_preview_html(agreement_type, version, company=None, user=None):
    """Renders the unsigned document body (with an unsealed 'Not Yet Signed'
    placeholder instead of the digital seal) for inline review before the
    user commits to signing.
    """
    template_name = TEMPLATE_BY_TYPE.get(agreement_type)
    if not template_name:
        raise ValueError(f"No PDF template configured for {agreement_type}")

    context = {
        "version": version,
        "generated_at": timezone.now().strftime("%Y-%m-%d %H:%M UTC"),
        "company_name": getattr(company, "name", ""),
        "company_registration_number": getattr(company, "registration_number", ""),
        "company_country": getattr(company, "country", ""),
        "signatory_name": "",
        "is_preview": True,
        **_individual_context(user),
    }
    return render_to_string(template_name, context)


def render_agreement_pdf(agreement, company=None, user=None, request=None):
    """Renders `agreement` (already marked SIGNED, with contract_id set) to
    a PDF using the template for its agreement_type, embeds the digital
    seal (contract id, signatory, timestamp, IP, OTP ref, QR code) and the
    company stamp where applicable, and attaches the result as
    `agreement.signed_pdf`.
    """
    from weasyprint import HTML

    template_name = TEMPLATE_BY_TYPE.get(agreement.agreement_type)
    if not template_name:
        raise ValueError(f"No PDF template configured for {agreement.agreement_type}")

    verification_url = f"{settings.FRONTEND_URL}/en/verify-agreement?id={agreement.contract_id}"

    stamp_data_uri = None
    if agreement.agreement_type in STAMPED_TYPES and company is not None:
        stamp_data_uri = _file_to_data_uri(getattr(company, "stamp_image", None))

    context = {
        "version": agreement.version,
        "generated_at": timezone.now().strftime("%Y-%m-%d %H:%M UTC"),
        "company_name": getattr(company, "name", ""),
        "company_registration_number": getattr(company, "registration_number", ""),
        "company_country": getattr(company, "country", ""),
        "signatory_name": agreement.signatory_name,
        "contract_id": agreement.contract_id,
        "signed_at": agreement.accepted_at.strftime("%Y-%m-%d %H:%M UTC") if agreement.accepted_at else "",
        "ip_address": agreement.ip_address or "",
        "otp_reference": agreement.otp_reference,
        "verification_url": verification_url,
        "qr_data_uri": _build_qr_data_uri(verification_url),
        "stamp_data_uri": stamp_data_uri,
        **_individual_context(user),
    }

    html_string = render_to_string(template_name, context)
    pdf_bytes = HTML(string=html_string).write_pdf()

    filename = f"{agreement.agreement_type.lower()}_{agreement.version}_{int(timezone.now().timestamp())}.pdf"
    agreement.signed_pdf.save(filename, ContentFile(pdf_bytes), save=False)
    return agreement
