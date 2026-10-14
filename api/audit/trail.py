"""Activity trail of one candidate: every audit entry tied to the candidate
or anything under it (interview sessions, answers, evaluations, reports,
certificates), and a provenance summary - who registered the candidate and
from which IP, who created each assessment and from where, and which IPs
the candidate used on the interview link."""
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q

from api.core.constants import AuditLogAction
from .models import AuditLog


def _related(candidate):
    from api.evaluations.models import Certificate, Evaluation
    from api.reports.models import EvaluationReport
    from api.sessions.models import CandidateResponse, InterviewSession

    sessions = InterviewSession.objects.filter(candidate=candidate)
    return [
        (type(candidate), [candidate.pk]),
        (InterviewSession, sessions.values_list("pk", flat=True)),
        (CandidateResponse, CandidateResponse.objects.filter(session__in=sessions).values_list("pk", flat=True)),
        (Evaluation, Evaluation.objects.filter(candidate=candidate).values_list("pk", flat=True)),
        (EvaluationReport, EvaluationReport.objects.filter(candidate=candidate).values_list("pk", flat=True)),
        (Certificate, Certificate.objects.filter(candidate=candidate).values_list("pk", flat=True)),
    ]


def candidate_entries_filter(candidate):
    """Q matching every audit entry tied to this candidate."""
    condition = Q(pk__in=[])
    for model, ids in _related(candidate):
        condition |= Q(resource_type=ContentType.objects.get_for_model(model), resource_id__in=list(ids))
    return condition


def candidate_for_resource(resource):
    """The candidate an audit entry's resource belongs to, if any."""
    from api.candidates.models import Candidate

    if resource is None:
        return None
    if isinstance(resource, Candidate):
        return resource
    if hasattr(resource, "candidate_id"):
        return resource.candidate
    session = getattr(resource, "session", None)
    return getattr(session, "candidate", None)


def _origin(entry):
    if entry is None:
        return None
    return {"at": entry.created_at, "by_email": entry.user_email, "by_name": entry.user_name,
            "ip_address": entry.ip_address, "user_agent": entry.user_agent}


def candidate_trail(candidate):
    from api.evaluations.models import Evaluation
    from api.sessions.models import InterviewSession

    session_type = ContentType.objects.get_for_model(InterviewSession)
    candidate_type = ContentType.objects.get_for_model(type(candidate))
    registration = (AuditLog.objects.filter(resource_type=candidate_type, resource_id=candidate.pk,
                                            action=AuditLogAction.CANDIDATE_CREATED)
                    .order_by("created_at").first())

    assessments = []
    for session in (InterviewSession.objects.filter(candidate=candidate).select_related("created_by")
                    .order_by("created_at")):
        on_session = AuditLog.objects.filter(resource_type=session_type, resource_id=session.pk)
        created = on_session.filter(action=AuditLogAction.SESSION_CREATED).order_by("created_at").first()
        # The candidate has no account: their interview-link requests are the
        # entries with an origin but no user.
        candidate_ips = sorted(set(
            on_session.filter(user__isnull=True, ip_address__isnull=False)
            .order_by().values_list("ip_address", flat=True)
        ))
        evaluation = Evaluation.objects.filter(session=session).first()
        assessments.append({
            "session_id": str(session.public_id),
            "evaluation_id": str(evaluation.public_id) if evaluation is not None else None,
            "role_code": session.role_code,
            "evaluation_tier": session.evaluation_tier,
            "status": session.status,
            "created_at": session.created_at,
            "created_by_email": session.created_by.email if session.created_by_id else None,
            "created_origin": _origin(created),
            "started_at": session.started_at,
            "ended_at": session.ended_at,
            "candidate_ips": candidate_ips,
        })

    owner = candidate.created_by
    return {
        "candidate": {
            "id": str(candidate.public_id),
            "name": candidate.get_full_name(),
            "passport_id": candidate.passport_id,
            "email": candidate.email,
            "status": candidate.status,
            "created_at": candidate.created_at,
            "owner_email": owner.email if owner else None,
            "owner_name": owner.get_full_name() if owner else None,
            "company": candidate.company.name if candidate.company_id else None,
        },
        # None means the candidate was not added through the app (no
        # CANDIDATE_CREATED entry) - e.g. inserted directly on the server.
        "registration": _origin(registration),
        "assessments": assessments,
        "event_count": AuditLog.objects.filter(candidate_entries_filter(candidate)).count(),
    }
