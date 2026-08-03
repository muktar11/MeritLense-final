from rest_framework.test import APIClient, APITestCase

from api.accounts.models import User
from api.core.constants import Roles
from api.dashboard.models import AdminAlertConfiguration


class AdminAlertConfigurationApiTests(APITestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = User.objects.create_user(
            email="admin@example.com",
            password="testpass123",
            first_name="Admin",
            last_name="User",
            role=Roles.ADMIN,
            is_verified=True,
        )
        self.b2c_user = User.objects.create_user(
            email="user@example.com",
            password="testpass123",
            first_name="Regular",
            last_name="User",
            role=Roles.B2C,
            is_verified=True,
        )

    def test_admin_can_get_and_update_alert_configuration(self):
        self.client.force_authenticate(self.admin)

        get_response = self.client.get("/api/v1/dashboard/admin/alert-configuration")
        self.assertEqual(get_response.status_code, 200, get_response.data)
        self.assertEqual(get_response.data["settings"], {})

        patch_response = self.client.patch(
            "/api/v1/dashboard/admin/alert-configuration",
            {
                "settings": {
                    "email_alerts_enabled": True,
                    "payment_failure_alerts_enabled": True,
                    "system_incident_alerts_enabled": False,
                }
            },
            format="json",
        )
        self.assertEqual(patch_response.status_code, 200, patch_response.data)
        self.assertEqual(
            patch_response.data["settings"],
            {
                "email_alerts_enabled": True,
                "payment_failure_alerts_enabled": True,
                "system_incident_alerts_enabled": False,
            },
        )

        config = AdminAlertConfiguration.objects.get(singleton_key="default")
        self.assertEqual(config.settings["payment_failure_alerts_enabled"], True)

    def test_non_admin_cannot_access_alert_configuration(self):
        self.client.force_authenticate(self.b2c_user)

        response = self.client.get("/api/v1/dashboard/admin/alert-configuration")
        self.assertEqual(response.status_code, 403)
