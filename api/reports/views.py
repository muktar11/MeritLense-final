from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory, AuditLogSeverity, Roles
from api.core.public_ids import PublicIdLookupMixin
from api.reports.models import EvaluationReport
from api.reports.serializers import EvaluationReportListSerializer, EvaluationReportSerializer
from api.reports.services import EvaluationReportError, EvaluationReportService


class EvaluationReportViewSet(PublicIdLookupMixin, viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = EvaluationReport.objects.select_related(
            "evaluation",
            "session",
            "candidate",
            "generated_by",
        )
        if user.role in [Roles.ADMIN, Roles.SUPERADMIN]:
            return queryset
        if user.role == Roles.B2C:
            return queryset.filter(evaluation__created_by=user)
        if user.role == Roles.B2B:
            if hasattr(user, "company_profile"):
                return queryset.filter(evaluation__company=user.company_profile.company)
            return queryset.none()
        if user.role == Roles.B2B_TEAM_MEMBER:
            return queryset.filter(
                Q(evaluation__created_by=user) | Q(candidate__shared_with=user)
            ).distinct()
        return queryset.none()

    def get_serializer_class(self):
        if self.action == "list":
            return EvaluationReportListSerializer
        return EvaluationReportSerializer

    def retrieve(self, request, *args, **kwargs):
        report = self.get_object()
        AuditLogService.log(
            user=request.user,
            action=AuditLogAction.REPORT_VIEWED,
            category=AuditLogCategory.EVALUATION,
            description=f"Report viewed for evaluation {report.evaluation.public_id}",
            resource=report,
            data={
                "report_id": str(report.public_id),
                "report_number": report.report_number,
                "evaluation_id": str(report.evaluation.public_id),
                "session_id": str(report.session.public_id),
            },
            request=request,
        )
        return Response(self.get_serializer(report).data)

    @action(detail=True, methods=["post"], url_path="regenerate")
    def regenerate(self, request, id=None):
        report = self.get_object()
        try:
            new_report = EvaluationReportService.generate_for_evaluation(
                evaluation=report.evaluation,
                actor=request.user,
            )
        except EvaluationReportError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(EvaluationReportSerializer(new_report).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="export-payload")
    def export_payload(self, request, id=None):
        report = self.get_object()
        AuditLogService.log(
            user=request.user,
            action=AuditLogAction.REPORT_EXPORT_PAYLOAD_REQUESTED,
            category=AuditLogCategory.EVALUATION,
            description=f"Report export payload requested for evaluation {report.evaluation.public_id}",
            resource=report,
            data={
                "report_id": str(report.public_id),
                "evaluation_id": str(report.evaluation.public_id),
                "report_version": report.report_version,
            },
            request=request,
        )
        return Response(EvaluationReportService.export_payload(report))


def deny_report_access(*, user, evaluation=None, report=None, request=None, reason="Unauthorized report access"):
    resource = report or evaluation
    AuditLogService.log(
        user=user,
        action=AuditLogAction.REPORT_ACCESS_DENIED,
        category=AuditLogCategory.EVALUATION,
        description=reason,
        resource=resource,
        severity=AuditLogSeverity.WARNING,
        data={
            "evaluation_id": str(evaluation.public_id) if evaluation is not None else None,
            "report_id": str(report.public_id) if report is not None else None,
        },
        request=request,
    )
    raise PermissionDenied("You do not have access to this report")
