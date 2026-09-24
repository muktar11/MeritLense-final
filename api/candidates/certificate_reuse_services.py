"""Cross-account certificate reuse.

Candidate.passport_id is globally unique across the whole platform (not
scoped per company/user) - the same real person can only ever have one
Candidate row and one evaluation history. When a second employer tries to
add a candidate by a passport_id that already belongs to a DIFFERENT
account, they can't create their own duplicate Candidate row (that would
violate the DB uniqueness constraint) - instead they're offered that
candidate's existing, already-issued certificate, and can pay one
Assessment Slot to access it rather than running a brand new interview.
See CertificateAccessGrant (api/evaluations/models.py) for the record of
that access, and EntitlementService.consume_slot_for_certificate_reuse for
the Slot deduction itself.
"""
from api.core.constants import CertificateStatus
from api.evaluations.models import Certificate, CertificateAccessGrant, Evaluation
from .models import Candidate


class CertificateReuseError(Exception):
    pass


def find_reusable_certificate(*, passport_id, requesting_company, requesting_user):
    """Returns (existing_candidate, certificate) if a DIFFERENT account
    already owns a candidate with this passport_id who has an issued
    certificate - (existing_candidate, None) if the candidate exists
    elsewhere but has no certificate to offer - or (None, None) if no
    candidate with this passport_id exists anywhere, meaning creation can
    proceed normally.

    "Different account" excludes the requester's own scope (same company,
    or same creator for a B2C/no-company account) - a same-scope match is
    the ordinary within-account duplicate CandidateCreateSerializer.validate
    already handles with its own clear error, not a reuse offer."""
    existing = Candidate.objects.filter(passport_id=passport_id).first()
    if existing is None:
        return None, None

    if requesting_company:
        if existing.company_id == requesting_company.id:
            return None, None
    elif existing.created_by_id == requesting_user.id and existing.company_id is None:
        return None, None

    certificate = (
        Certificate.objects.filter(
            candidate=existing,
            evaluation__certificate_status=CertificateStatus.ISSUED,
            evaluation__is_deleted=False,
        )
        .select_related("evaluation")
        .order_by("-issued_at", "-created_at")
        .first()
    )
    return existing, certificate


def certificate_preview(certificate):
    return {
        "candidate_name": certificate.candidate.get_full_name(),
        "job_role": certificate.evaluation.get_candidate_job_role_display(),
        "issued_at": certificate.issued_at,
        "certificate_public_id": str(certificate.public_id),
    }


def grant_certificate_access(*, certificate, requesting_user, owner_type, owner, reference):
    """Persists the CertificateAccessGrant row after the Slot has already
    been deducted (see EntitlementService.consume_slot_for_certificate_reuse)
    - this call itself never touches the balance, it only records that the
    grant happened and why."""
    grant = CertificateAccessGrant.objects.create(
        certificate=certificate,
        granted_to_company=owner if owner_type == "COMPANY" else None,
        granted_to_user=None if owner_type == "COMPANY" else owner,
        requested_by=requesting_user,
        slot_transaction_reference=reference,
    )
    return grant
