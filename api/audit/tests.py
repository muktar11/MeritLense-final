from rest_framework import status
from rest_framework.test import APITestCase

from api.accounts.models import User
from api.core.constants import AuditLogAction, AuditLogCategory, Roles
from .models import AuditLog


class AuditLogFilterValidationTests(APITestCase):
    def setUp(self):
        self.superadmin = User.objects.create_user(
            email="audit-superadmin@example.com",
            password="Password123!",
            first_name="Audit",
            last_name="Super",
            role=Roles.SUPERADMIN,
            is_verified=True,
            is_staff=True,
        )
        login = self.client.post(
            "/api/v1/auth/login",
            {"email": self.superadmin.email, "password": "Password123!"},
            format="json",
        )
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")

        AuditLog.objects.create(
            user=self.superadmin,
            action=AuditLogAction.USER_UPDATED,
            category=AuditLogCategory.USER,
            description="Test log entry",
        )

    def test_invalid_resource_id_returns_400_instead_of_the_full_unfiltered_list(self):
        """Regression test: get_queryset() used to call filters.is_valid()
        without checking the result - a malformed filter param (e.g. a
        non-numeric resource_id) was silently ignored and the FULL
        unfiltered log list was returned with a 200."""
        response = self.client.get("/api/v1/audit/logs", {"resource_id": "not-a-number"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_valid_filters_still_work(self):
        response = self.client.get("/api/v1/audit/logs", {"category": AuditLogCategory.USER})
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertGreaterEqual(response.data["count"], 1)
