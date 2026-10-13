"""Request handling shared by the B2B, B2C and admin Candidate Comparison
views - the same parameters, validation and response shape for all three,
so the on-screen comparison and the PDF can never drift apart."""
from datetime import date

from django.http import HttpResponse
from rest_framework.response import Response

from .comparison_pdf_services import render_comparison_pdf
from .comparison_services import (
    ComparisonError,
    build_full_comparison,
    comparison_limitations,
    compute_key_differences,
    get_comparable_assessments,
)


def _id_list(request, name):
    raw = request.query_params.get(name, '')
    return [v.strip() for v in raw.split(',') if v.strip()]


def _parse_date(value):
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def comparison_language(request):
    return "ar" if request.query_params.get('lang') == "ar" else "en"


def assessments_response(request, *, owner_type, owner):
    """Step 2: the role's comparable assessments, with optional
    `evaluation_tier`, `date_from` and `date_to` (YYYY-MM-DD) filters."""
    role_code = request.query_params.get('role_code', '').strip()
    if not role_code:
        return Response({'error': 'role_code is required'}, status=400)
    tier = request.query_params.get('evaluation_tier', '').strip().upper()
    if tier and tier not in ('FULL', 'SCREENING'):
        return Response({'error': 'evaluation_tier must be FULL or SCREENING'}, status=400)
    return Response(get_comparable_assessments(
        owner_type=owner_type, owner=owner, role_code=role_code, evaluation_tier=tier or None,
        date_from=_parse_date(request.query_params.get('date_from')),
        date_to=_parse_date(request.query_params.get('date_to')),
    ))


def comparison_response(request, *, owner_type, owner, role_name_for, pdf=False, on_entries=None):
    """Steps 3+4 (or the PDF of them). Takes `evaluation_ids` - the
    specific assessments picked - or, for older callers, `candidate_ids`;
    2-4 either way. `role_name_for(role_code)` gives the display name;
    `on_entries(role_code, entries)` lets the admin views audit-log."""
    role_code = request.query_params.get('role_code', '').strip()
    evaluation_ids = _id_list(request, 'evaluation_ids')
    candidate_ids = _id_list(request, 'candidate_ids')
    count = len(evaluation_ids or candidate_ids)
    if not role_code or not (2 <= count <= 4):
        return Response({'error': 'role_code and 2-4 evaluation_ids (or candidate_ids) are required'}, status=400)

    language = comparison_language(request)
    try:
        entries = build_full_comparison(
            owner_type=owner_type, owner=owner, role_code=role_code, language=language, actor=request.user,
            evaluation_ids=evaluation_ids or None, candidate_ids=None if evaluation_ids else candidate_ids,
        )
    except ComparisonError as exc:
        return Response({'error': str(exc), 'code': 'INCOMPATIBLE_SELECTION'}, status=400)

    if pdf and len(entries) < 2:
        return Response({'error': 'At least 2 comparable candidates are required.'}, status=400)
    if on_entries is not None:
        on_entries(role_code, entries)

    role_name = role_name_for(role_code)
    key_differences = compute_key_differences(entries, language=language)
    limitations = comparison_limitations(entries, language=language)
    if pdf:
        pdf_bytes = render_comparison_pdf(
            role_name=role_name, entries=entries, key_differences=key_differences,
            limitations=limitations, language=language,
        )
        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = 'attachment; filename="candidate-comparison.pdf"'
        return response
    return Response({
        'role_code': role_code,
        'role_name': role_name,
        'evaluation_tier': entries[0]['evaluation_tier'] if entries else '',
        'candidates': entries,
        'key_differences': key_differences,
        'limitations': limitations,
    })
