from django.db import transaction
from django.utils import timezone

from api.core.constants import AuditLogAction, AuditLogCategory, PackageRequestStatus
from .models import DealRecord, PackageRequest


class PackageRequestError(Exception):
    pass


class PackageRequestService:
    """Submit -> review -> decide lifecycle for a company's Starter/
    Enterprise ask. Approval is the one place a DealRecord gets created
    from a request - the SuperAdmin's own numbers at that moment, not the
    company's original ask, are what actually get granted (see
    PackageRequest's own docstring)."""

    @classmethod
    def submit(cls, *, company, requested_by, deal_type, requested_slot_grant=None, requested_points_grant=None, message=""):
        from api.accounts.utils import notify_superadmins
        from api.audit.services import AuditLogService

        package_request = PackageRequest.objects.create(
            company=company,
            requested_by=requested_by,
            deal_type=deal_type,
            requested_slot_grant=requested_slot_grant,
            requested_points_grant=requested_points_grant,
            message=message,
        )

        AuditLogService.log(
            user=requested_by,
            action=AuditLogAction.PACKAGE_REQUEST_SUBMITTED,
            category=AuditLogCategory.SUBSCRIPTION,
            description=f"Package request submitted: {company.name} ({deal_type})",
            resource=package_request,
            data={"deal_type": deal_type, "requested_slot_grant": requested_slot_grant, "requested_points_grant": requested_points_grant},
        )

        notify_superadmins(
            subject=f"New {package_request.get_deal_type_display()} request from {company.name}",
            message=(
                f"{requested_by.get_full_name()} ({requested_by.email}) at {company.name} has requested a "
                f"{package_request.get_deal_type_display()} package.\n\n"
                f"Requested slots: {requested_slot_grant if requested_slot_grant is not None else 'not specified'}\n"
                f"Requested points: {requested_points_grant if requested_points_grant is not None else 'not specified'}\n\n"
                f"Message from requester:\n{message or '(none)'}\n\n"
                "Review it in the SuperAdmin dashboard."
            ),
        )
        return package_request

    @classmethod
    @transaction.atomic
    def approve(cls, *, package_request, actor, slot_grant, points_grant, unit_amount, currency="eur", rollover_allowed=False, addendum_reference="", decision_reason=""):
        from api.accounts.utils import send_package_request_approved_email
        from api.audit.services import AuditLogService

        package_request = PackageRequest.objects.select_for_update().get(pk=package_request.pk)
        if package_request.status != PackageRequestStatus.PENDING:
            raise PackageRequestError(f"This request has already been {package_request.status.lower()}.")

        deal_record = DealRecord.objects.create(
            company=package_request.company,
            deal_type=package_request.deal_type,
            slot_grant=slot_grant,
            points_grant=points_grant,
            unit_amount=unit_amount,
            currency=currency,
            rollover_allowed=rollover_allowed,
            addendum_reference=addendum_reference,
            confirmation_note=f"Auto-created from package request {package_request.public_id} on approval.",
            created_by=actor,
        )

        package_request.status = PackageRequestStatus.APPROVED
        package_request.decision_reason = decision_reason
        package_request.reviewed_by = actor
        package_request.reviewed_at = timezone.now()
        package_request.deal_record = deal_record
        package_request.save(update_fields=["status", "decision_reason", "reviewed_by", "reviewed_at", "deal_record", "updated_at"])

        AuditLogService.log(
            user=actor,
            action=AuditLogAction.PACKAGE_REQUEST_APPROVED,
            category=AuditLogCategory.SUBSCRIPTION,
            description=f"Package request approved: {package_request.company.name} ({package_request.deal_type})",
            resource=package_request,
            data={"slot_grant": slot_grant, "points_grant": points_grant, "unit_amount": str(unit_amount), "deal_record_id": deal_record.id},
        )

        send_package_request_approved_email(package_request.requested_by, package_request, deal_record)
        return package_request

    @classmethod
    def deny(cls, *, package_request, actor, decision_reason):
        from api.accounts.utils import send_package_request_denied_email
        from api.audit.services import AuditLogService

        if not decision_reason:
            raise PackageRequestError("A reason is required when denying a package request.")

        with transaction.atomic():
            package_request = PackageRequest.objects.select_for_update().get(pk=package_request.pk)
            if package_request.status != PackageRequestStatus.PENDING:
                raise PackageRequestError(f"This request has already been {package_request.status.lower()}.")

            package_request.status = PackageRequestStatus.DENIED
            package_request.decision_reason = decision_reason
            package_request.reviewed_by = actor
            package_request.reviewed_at = timezone.now()
            package_request.save(update_fields=["status", "decision_reason", "reviewed_by", "reviewed_at", "updated_at"])

        AuditLogService.log(
            user=actor,
            action=AuditLogAction.PACKAGE_REQUEST_DENIED,
            category=AuditLogCategory.SUBSCRIPTION,
            description=f"Package request denied: {package_request.company.name} ({package_request.deal_type})",
            resource=package_request,
            data={"decision_reason": decision_reason},
        )

        send_package_request_denied_email(package_request.requested_by, package_request, decision_reason)
        return package_request
