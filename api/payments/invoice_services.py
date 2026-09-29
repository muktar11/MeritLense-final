import base64
import hashlib
import re
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.template.loader import render_to_string

from api.core.constants import InterviewEvaluationTier, PaymentMethodConstants
from api.core.pdf_fonts import arabic_font_context

# The full icon+wordmark lockup (not the icon-only mark
# certificate_services.py/reports use) - the invoice header shows the
# brand name as part of the logo image itself, with the tagline as
# separate text below it, matching the approved letterhead design.
_LOGO_PATH = Path(__file__).resolve().parents[1] / "evaluations" / "assets" / "meritlense-logo-full.png"
_logo_data_uri_cache = None


def _logo_data_uri():
    global _logo_data_uri_cache
    if _logo_data_uri_cache is None:
        encoded = base64.b64encode(_LOGO_PATH.read_bytes()).decode()
        _logo_data_uri_cache = f"data:image/png;base64,{encoded}"
    return _logo_data_uri_cache


# Static "Supplier / From" box content - MeritLense's own info never varies
# per-invoice. Tax / VAT ID is still genuinely not finalized (kept here as
# a single place to fill in once it is) - the registered address is final.
SUPPLIER = {
    "name": "MeritLense OÜ",
    "description": "Workforce Readiness Assessment Platform",
    "description_ar": "منصة تقييم جاهزية القوى العاملة",
    "address": "Ruunaoja tn 3, 11415 Tallinn, Estonia",
    "tax_id": None,
    "email": "info@meritlense.com",
}

BANK_DETAILS = {
    "account_holder": None,
    "iban": None,
    "bic_swift": None,
}

# MeritLense OÜ is not currently VAT-registered. While that's true, no
# invoice should represent a transaction as "0% VAT" - that implies a real,
# zero-rated VAT treatment, which is a different legal claim than "VAT does
# not apply because the supplier isn't registered". Once registration
# happens, the applicable rate/treatment must be computed per-transaction
# (customer type/location) - never hard-coded back to a single flag/rate
# here; this constant only governs today's genuinely-uniform not-registered
# state.
SUPPLIER_VAT_REGISTERED = False


class InvoicePdfError(Exception):
    pass


def _invoice_language(invoice):
    profile = getattr(invoice.user, "company_profile", None) or getattr(invoice.user, "individual_profile", None)
    language = getattr(profile, "preferred_language", None) or "EN"
    return "ar" if str(language).upper() == "AR" else "en"


def _is_arabic_text(text):
    return bool(text) and bool(re.search(r"[؀-ۿ]", text))


def _translate_address_to_arabic(address):
    """Company/individual addresses are stored in whatever script the
    profile was filled in with (almost always English/Latin - city and
    country names, street names) - on an Arabic invoice this read as
    English text sitting inside an Arabic document, not a translated one.
    Live-translates via the same TranslationService/Google provider
    already used for Evidence Summary phrases (see
    TranslationService.translate_indicator_phrase) - no caching table here
    since addresses are per-company and generated rarely (once per
    invoice), unlike the unbounded free-form rubric vocabulary that needed
    one. Never raises: falls back to the original address on any failure
    (missing provider config, network error, etc.), same non-blocking
    contract every other translation call in report/certificate
    generation already follows."""
    if not address or _is_arabic_text(address):
        return address
    try:
        from api.translation.services import TranslationService

        result = TranslationService.translate(text=address, source_language="en", target_language="ar")
        return result.get("translated_text") or address
    except Exception:
        return address


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


def _package_description(price):
    """"MeritLense <Package> — <N> [Full ]Assessment(s)[ / Month]" - built
    from the actual purchased Price, not a generic "MeritLense
    subscription"/raw internal price name. The Full/plain wording mirrors
    the package's own evaluation_tier (Screening packages read as plain
    "Assessments", matching how the pricing page itself only calls out
    "Full" for FULL-tier packages); billing_type adds "/ Month" only for
    recurring (B2B) plans, never for a one-time purchase."""
    if price is None:
        return None
    base_name = re.sub(r"\s+package$", "", price.name or "", flags=re.IGNORECASE).strip().title()
    if not base_name:
        return None
    count = price.slot_grant
    if not count:
        return f"MeritLense {base_name}"
    tier_word = "Full " if price.evaluation_tier == InterviewEvaluationTier.FULL else ""
    unit = "Assessment" if count == 1 else "Assessments"
    period_suffix = " / Month" if price.billing_type == "RECURRING" else ""
    return f"MeritLense {base_name} — {count} {tier_word}{unit}{period_suffix}"


def _payment_method_label(invoice):
    """"Visa •••• 4242" / "Bank Transfer" for the paid-invoice confirmation
    box - None (line omitted) when no payment method is resolvable, per
    the spec's own "Payment Method, where available" qualifier."""
    payment = invoice.stripe_payment_intent
    method = getattr(payment, "stripe_payment_method", None) if payment else None
    if method is None:
        return None
    if method.method_type == PaymentMethodConstants.CARD and method.card_brand and method.card_last4:
        return f"{method.card_brand.title()} •••• {method.card_last4}"
    return method.get_method_type_display()


def _line_items_context(invoice):
    """One synthetic line item per invoice - no LineItem model exists.
    VAT is always 0%/0.00, matching MeritLense not charging VAT today -
    the template decides whether to even display a VAT breakdown based on
    SUPPLIER_VAT_REGISTERED, so this stays a neutral, always-correct value
    rather than something the template has to reinterpret.
    Service period is shown only when the linked Subscription actually
    carries a genuine, distinct start/end (its real billing or purchase-
    validity window) - never a same-day issue/due-date fallback, which
    previously produced a misleading "period" of a single repeated date."""
    price = invoice.subscription.stripe_price if (invoice.subscription_id and invoice.subscription) else None
    description = _package_description(price) or "MeritLense subscription"

    show_service_period = False
    period_start = period_end = None
    if invoice.subscription_id and invoice.subscription:
        subscription = invoice.subscription
        if (
            subscription.current_period_start
            and subscription.current_period_end
            and subscription.current_period_start != subscription.current_period_end
        ):
            period_start = subscription.current_period_start
            period_end = subscription.current_period_end
            show_service_period = True

    net_amount = invoice.amount_due
    return [
        {
            "description": description,
            "qty": 1,
            "unit_price": net_amount,
            "net_amount": net_amount,
            "vat_percent": Decimal("0.00"),
            "vat_amount": Decimal("0.00"),
            "show_service_period": show_service_period,
            "period_start": period_start.strftime("%Y-%m-%d") if period_start else "",
            "period_end": period_end.strftime("%Y-%m-%d") if period_end else "",
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
    # The totals-section "VAT (X%)" label must read the rate that was
    # actually applied to these line items, not a literal string baked
    # into the template - otherwise a future non-zero/mixed transaction
    # tax treatment would render next to a stale "0%" label. Only
    # collapses to a single rate when every line item agrees; a future
    # multi-rate invoice (mixed VAT treatments in one invoice) falls back
    # to no percentage in the summary label, since the per-line VAT %
    # column already shows the real breakdown for that case.
    distinct_vat_percents = {item["vat_percent"] for item in line_items}
    vat_rate_label = str(next(iter(distinct_vat_percents))) if len(distinct_vat_percents) == 1 else None
    language = _invoice_language(invoice)
    billing_party = _billing_party_context(invoice)
    if language == "ar":
        # The company/individual's own name is a proper noun (like
        # "MeritLense" itself, never translated on this document) and
        # stays as entered - only the address (city/country/street names)
        # gets translated, since that's descriptive text, not an identity.
        billing_party["address"] = _translate_address_to_arabic(billing_party["address"])
    # Drives whether invoice_ar.html isolates this as an LTR run - only
    # needed when translation didn't happen/failed and the address is
    # still Latin script; real Arabic text must stay in the normal RTL
    # flow, not be forced into an LTR box.
    billing_party["address_is_latin"] = bool(billing_party["address"]) and not _is_arabic_text(billing_party["address"])

    # A PAID invoice must never show payment instructions/bank details -
    # those are actionable only while money is still owed. It shows a
    # confirmation box instead; method/date are individually omitted if
    # not resolvable rather than blocking the PAID status itself.
    is_paid = invoice.amount_remaining <= Decimal("0.00")
    payment_method_label = _payment_method_label(invoice) if is_paid else None
    payment_date = invoice.paid_at.strftime("%Y-%m-%d") if (is_paid and invoice.paid_at) else None

    return {
        "language": language,
        "invoice_number": invoice.number or invoice.stripe_invoice_id,
        "issue_date": issue_date.strftime("%Y-%m-%d") if issue_date else "",
        "supply_date": issue_date.strftime("%Y-%m-%d") if issue_date else "",
        "due_date": due_date.strftime("%Y-%m-%d") if due_date else "",
        "payable_by": due_date.strftime("%Y-%m-%d") if due_date else "",
        "currency": invoice.currency.upper(),
        "billing_party": billing_party,
        "is_paid": is_paid,
        "payment_method_label": payment_method_label,
        "payment_date": payment_date,
        "vat_registered": SUPPLIER_VAT_REGISTERED,
        "line_items": [
            {
                "description": item["description"],
                "qty": item["qty"],
                "unit_price": _money(item["unit_price"]),
                "net_amount": _money(item["net_amount"]),
                "vat_percent": str(item["vat_percent"]),
                "vat_amount": _money(item["vat_amount"]),
                "show_service_period": item["show_service_period"],
                "period_start": item["period_start"],
                "period_end": item["period_end"],
            }
            for item in line_items
        ],
        "subtotal": _money(subtotal),
        "vat_total": _money(vat_total),
        "vat_rate_label": vat_rate_label,
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
