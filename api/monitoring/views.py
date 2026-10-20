from rest_framework import mixins, viewsets
from rest_framework.permissions import IsAuthenticated

from api.audit.services import AuditLogService
from api.core.constants import AuditLogAction, AuditLogCategory
from api.core.permisssions import IsAdminOrSuperAdmin

from .models import ServiceIncident
from .serializers import ServiceIncidentSerializer


class ServiceIncidentViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, mixins.UpdateModelMixin, viewsets.GenericViewSet,
):
    """Administrator view of service incidents (rulebook EXC-06). Filter
    with ?status=OPEN|IN_PROGRESS|RESOLVED and ?cause=..."""

    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    serializer_class = ServiceIncidentSerializer
    lookup_field = "public_id"
    lookup_url_kwarg = "id"
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        queryset = ServiceIncident.objects.select_related(
            "session", "evaluation", "company", "account_user", "owner", "resolved_by",
        )
        status = self.request.query_params.get("status")
        if status:
            queryset = queryset.filter(status__in=status.split(","))
        cause = self.request.query_params.get("cause")
        if cause:
            queryset = queryset.filter(cause__in=cause.split(","))
        return queryset

    def perform_update(self, serializer):
        before = {"status": serializer.instance.status, "owner": getattr(serializer.instance.owner, "email", None)}
        incident = serializer.save()
        AuditLogService.log(
            user=self.request.user,
            action=AuditLogAction.SERVICE_INCIDENT_UPDATED,
            category=AuditLogCategory.SYSTEM,
            description=f"Service incident {incident.public_id} updated ({incident.status})",
            resource=incident,
            data={"before": before, "after": {"status": incident.status, "owner": getattr(incident.owner, "email", None)}},
        )
