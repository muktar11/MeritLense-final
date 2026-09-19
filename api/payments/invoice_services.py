import base64
import hashlib
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string

from api.core.pdf_fonts import arabic_font_context

# Reuses the same real logo asset certificate_services.py already bundles -
# one file, not a second copy per app.
_LOGO_PATH = Path(__file__).resolve().parents[1] / "evaluations" / "assets" / "meritlense-logo.png"
_logo_data_uri_cache = None


def _logo_data_uri():
    global _logo_data_uri_cache
    if _logo_data_uri_cache is None:
        encoded = base64.b64encode(_LOGO_PATH.read_bytes()).decode()
        _logo_data_uri_cache = f"data:image/png;base64,{encoded}"
    return _logo_data_uri_cache


# Static "Supplier / From" box content - MeritLense's own info never varies
# per-invoice. Registered address / VAT ID are genuinely not finalized yet
# (matches the "to be completed" state on the reference invoice this was
# modeled from), kept here as a single place to fill in once they are.
SUPPLIER = {
    "name": "MeritLense",
    "description": "Workforce Readiness Assessment Platform",
    "description_ar": "منصة تقييم جاهزية القوى العاملة",
    "address": None,
    "tax_id": None,
    "email": "info@meritlense.com",
}

BANK_DETAILS = {
    "account_holder": None,
    "iban": None,
    "bic_swift": None,
}


class InvoicePdfError(Exception):
    pass


def _invoice_language(invoice):
    profile = getattr(invoice.user, "company_profile", None) or getattr(invoice.user, "individual_profile", None)
    language = getattr(profile, "preferred_language", None) or "EN"
    return "ar" if str(language).upper() == "AR" else "en"


def _billing_party_context(invoice):
    """Billing name/address/email for the "Customer / Bill To" box.
    CompanyEmployerProfile/IndividualEmployerProfile are read directly (not
    via CompanyEmployerProfile.company, a separate, optional, nullable
    verified-Company record) - these profiles are the actual data attached
    to Invoice.user. No structured VAT/Tax-ID field exists anywhere in the
    codebase (only an uploaded tax document file, not usable as inline
    text), so tax_id always renders as not-provided."""
    user = invoice.user
    company_profile = getattr(user, "company_profile", None)
    individual_profile = getattr(user, "individual_profile", None)

    if company_profile is not None:
        name = company_profile.company_name or user.get_full_name()
        address_parts = [company_profile.address, company_profile.city, company_profile.country]
    elif individual_profile is not None:
        name = user.get_full_name()
        address_parts = [individual_profile.address]
    else:
        name = user.get_full_name()
        address_parts = []

    address = ", ".join(part for part in address_parts if part) or None
    return {"name": name, "address": address, "tax_id": None, "email": user.email}


def _line_items_context(invoice):
    """One synthetic line item per invoice - no LineItem model exists.
    VAT is always 0%/0.00, matching MeritLense not charging VAT today;
    revisit if that ever changes rather than deriving it from Stripe's own
    tax data, which isn't persisted on Invoice."""
    description = None
    if invoice.subscription_id and invoice.subscription and invoice.subscription.stripe_price:
        description = invoice.subscription.stripe_price.name
    description = description or "MeritLense subscription"
    net_amount = invoice.amount_due
    return [
        {
            "description": description,
            "qty": 1,
            "unit_price": net_amount,
            "net_amount": net_amount,
            "vat_percent": Decimal("0.00"),
            "vat_amount": Decimal("0.00"),
        }
    ]


def _money(value):
    return f"{Decimal(value):,.2f}"


def _build_snapshot(invoice):
    issue_date = invoice.paid_at or invoice.created_at
    due_date = invoice.due_date or issue_date
    line_items = _line_items_context(invoice)
    subtotal = sum((item["net_amount"] for item in line_items), Decimal("0.00"))
    vat_total = sum((item["vat_amount"] for item in line_items), Decimal("0.00"))
    return {
        "language": _invoice_language(invoice),
        "invoice_number": invoice.number or invoice.stripe_invoice_id,
        "issue_date": issue_date.strftime("%Y-%m-%d") if issue_date else "",
        "supply_date": issue_date.strftime("%Y-%m-%d") if issue_date else "",
        "due_date": due_date.strftime("%Y-%m-%d") if due_date else "",
        "payable_by": due_date.strftime("%Y-%m-%d") if due_date else "",
        "currency": invoice.currency.upper(),
        "billing_party": _billing_party_context(invoice),
        "line_items": [
            {
                "description": item["description"],
                "qty": item["qty"],
                "unit_price": _money(item["unit_price"]),
                "net_amount": _money(item["net_amount"]),
                "vat_percent": str(item["vat_percent"]),
                "vat_amount": _money(item["vat_amount"]),
            }
            for item in line_items
        ],
        "subtotal": _money(subtotal),
        "vat_total": _money(vat_total),
        "total": _money(subtotal + vat_total),
        "amount_paid": _money(invoice.amount_paid),
        "amount_due_display": _money(invoice.amount_remaining),
    }


def _render_pdf(snapshot):
    from weasyprint import HTML

    language = snapshot["language"]
    template_name = "payments/invoice_ar.html" if language == "ar" else "payments/invoice.html"
    context = {"invoice": snapshot, "supplier": SUPPLIER, "bank": BANK_DETAILS, "logo_data_uri": _logo_data_uri()}
    if language == "ar":
        context.update(arabic_font_context())
    html_string = render_to_string(template_name, context)
    pdf_bytes = HTML(string=html_string, base_url=str(getattr(settings, "BASE_DIR", ""))).write_pdf()
    return pdf_bytes, hashlib.sha256(pdf_bytes).hexdigest()


def generate_invoice_pdf(invoice):
    """Builds the local bilingual PDF for `invoice` from live data, snapshots
    the exact render context into pdf_render_snapshot, and saves both. Safe
    to call again later (e.g. via the generate_invoice_pdfs management
    command) - each call re-derives a fresh snapshot from current data. Use
    render_existing_invoice_pdf() instead to reproduce a previously issued
    PDF byte-for-byte from what was actually snapshotted."""
    snapshot = _build_snapshot(invoice)
    pdf_bytes, pdf_hash = _render_pdf(snapshot)
    filename = f"{snapshot['invoice_number']}.pdf"
    invoice.pdf_render_snapshot = snapshot
    invoice.pdf_hash = pdf_hash
    invoice.local_pdf_file.save(filename, ContentFile(pdf_bytes), save=False)
    invoice.save(update_fields=["pdf_render_snapshot", "pdf_hash", "local_pdf_file", "updated_at"])
    return invoice


def render_existing_invoice_pdf(invoice):
    """Rebuilds PDF bytes from the STORED snapshot - used by the download
    endpoint when local_pdf_file is missing (e.g. storage was wiped) but a
    snapshot still exists. Mirrors EvaluationReportService.render_existing_pdf."""
    if not invoice.pdf_render_snapshot:
        raise InvoicePdfError(
            "This invoice PDF is unavailable and cannot be rebuilt because its stored snapshot is missing."
        )
    return _render_pdf(invoice.pdf_render_snapshot)
