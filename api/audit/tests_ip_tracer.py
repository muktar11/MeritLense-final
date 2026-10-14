"""Activity IP tracing: the recorded IP can't be forged through
X-Forwarded-For, every audit entry written during a request carries its
origin, sign-ins are recorded, and admins can trace an account's IPs or an
IP's accounts."""
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from api.accounts.models import User
from api.core.constants import AuditLogAction, AuditLogCategory, Roles
from .models import AuditLog
from .request_context import AuditRequestContextMiddleware, get_client_ip
from .services import AuditLogService

PASSWORD = "Password123!"


def make_user(email, role=Roles.B2C):
    return User.objects.create_user(email=email, password=PASSWORD, first_name="T", last_name="U", role=role,
                                    is_verified=True, is_staff=role in (Roles.ADMIN, Roles.SUPERADMIN))


class ClientIpTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def test_a_forged_forwarded_entry_is_ignored(self):
        # nginx appends the address it saw; the client's own header is left of it.
        request = self.factory.get("/", HTTP_X_FORWARDED_FOR="203.0.113.77, 196.190.62.22", REMOTE_ADDR="127.0.0.1")
        self.assertEqual(get_client_ip(request), "196.190.62.22")

    def test_single_entry_and_no_proxy(self):
        self.assertEqual(get_client_ip(self.factory.get("/", HTTP_X_FORWARDED_FOR="196.190.62.22")), "196.190.62.22")
        self.assertEqual(get_client_ip(self.factory.get("/", REMOTE_ADDR="10.1.2.3")), "10.1.2.3")

    def test_garbage_is_dropped_not_stored(self):
        request = self.factory.get("/", HTTP_X_FORWARDED_FOR="not-an-ip", REMOTE_ADDR="127.0.0.1")
        self.assertIsNone(get_client_ip(request))

    @override_settings(AUDIT_TRUSTED_PROXY_COUNT=2)
    def test_two_trusted_proxies(self):
        request = self.factory.get("/", HTTP_X_FORWARDED_FOR="203.0.113.77, 196.190.62.22, 10.0.0.5")
        self.assertEqual(get_client_ip(request), "196.190.62.22")

    def test_entries_written_during_a_request_get_its_origin_without_passing_it(self):
        def view(request):
            AuditLogService.log_system(action=AuditLogAction.SESSION_READY, category=AuditLogCategory.SESSION,
                                       description="candidate on interview link")
            return HttpResponse()

        request = self.factory.post("/api/v1/interviews/x/start", HTTP_X_FORWARDED_FOR="196.190.62.22",
                                    HTTP_USER_AGENT="Candidate Browser")
        AuditRequestContextMiddleware(view)(request)
        entry = AuditLog.objects.get(description="candidate on interview link")
        self.assertEqual(entry.ip_address, "196.190.62.22")
        self.assertEqual(entry.user_agent, "Candidate Browser")
        self.assertEqual(entry.request_path, "/api/v1/interviews/x/start")

        # Outside a request (management commands) nothing leaks from the last one.
        AuditLogService.log_system(action=AuditLogAction.SESSION_READY, category=AuditLogCategory.SESSION,
                                   description="background")
        self.assertIsNone(AuditLog.objects.get(description="background").ip_address)


class LoginAuditTests(APITestCase):
    def test_successful_and_failed_sign_ins_are_recorded_with_the_ip(self):
        user = make_user("ip-login@example.com")
        ok = self.client.post("/api/v1/auth/login", {"email": user.email, "password": PASSWORD}, format="json",
                              HTTP_X_FORWARDED_FOR="203.0.113.9, 196.190.62.22")
        self.assertEqual(ok.status_code, 200)
        bad = self.client.post("/api/v1/auth/login", {"email": user.email, "password": "wrong"}, format="json",
                               HTTP_X_FORWARDED_FOR="41.0.0.8")
        self.assertEqual(bad.status_code, 401)
        self.client.post("/api/v1/auth/login", {"email": "ghost@example.com", "password": "x"}, format="json")

        success = AuditLog.objects.get(action=AuditLogAction.LOGIN_SUCCESS, user=user)
        self.assertEqual(success.ip_address, "196.190.62.22")
        self.assertEqual(success.data["login_method"], "password")
        failed = AuditLog.objects.get(action=AuditLogAction.LOGIN_FAILED, user=user)
        self.assertEqual(failed.ip_address, "41.0.0.8")
        self.assertTrue(AuditLog.objects.filter(action=AuditLogAction.LOGIN_FAILED, user__isnull=True,
                                                data__email="ghost@example.com").exists())


class IpActivityTests(APITestCase):
    def setUp(self):
        self.admin = make_user("ip-admin@example.com", Roles.SUPERADMIN)
        self.customer = make_user("ip-customer@example.com")
        self.other = make_user("ip-other@example.com")
        for user, ip, agent in [(self.customer, "196.190.62.22", "Chrome"), (self.customer, "196.190.62.22", "Chrome"),
                                (self.customer, "102.213.68.133", "Safari"), (self.other, "196.190.62.22", "Edge")]:
            AuditLog.objects.create(user=user, user_email=user.email, action=AuditLogAction.VIEW_CANDIDATES_LIST,
                                    category=AuditLogCategory.CANDIDATE, description="x", ip_address=ip,
                                    user_agent=agent)
        AuditLog.objects.create(user=self.customer, user_email=self.customer.email,
                                action=AuditLogAction.LOGIN_SUCCESS, category=AuditLogCategory.SECURITY,
                                description="x", ip_address="102.213.68.133")

    def _as(self, user):
        token = self.client.post("/api/v1/auth/login", {"email": user.email, "password": PASSWORD},
                                 format="json").data["access"]
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_account_mode_lists_each_ip_with_counts_and_shared_accounts(self):
        self._as(self.admin)
        response = self.client.get("/api/v1/audit/logs/ip-activity", {"email": self.customer.email})
        self.assertEqual(response.status_code, 200, response.data)
        rows = {row["ip_address"]: row for row in response.data["ips"]}
        self.assertEqual(set(rows), {"196.190.62.22", "102.213.68.133"})
        self.assertEqual(rows["196.190.62.22"]["events"], 2)
        self.assertEqual(rows["196.190.62.22"]["user_agents"], ["Chrome"])
        self.assertEqual(rows["196.190.62.22"]["other_accounts"], 1)
        self.assertEqual(rows["102.213.68.133"]["logins"], 1)

    def test_ip_mode_lists_every_account_on_that_ip(self):
        self._as(self.admin)
        response = self.client.get("/api/v1/audit/logs/ip-activity", {"ip": "196.190.62.22"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual({row["email"] for row in response.data["accounts"]},
                         {self.customer.email, self.other.email})
        self.assertEqual(self.client.get("/api/v1/audit/logs", {"ip_address": "102.213.68.133"}).data["count"], 2)

    def test_bad_input_and_non_admins_are_refused(self):
        self._as(self.admin)
        bad = self.client.get("/api/v1/audit/logs/ip-activity", {"ip": "nope"})
        self.assertEqual((bad.status_code, bad.data["code"]), (400, "invalid_query"))
        missing = self.client.get("/api/v1/audit/logs/ip-activity", {"email": "no@x.com"})
        self.assertEqual((missing.status_code, missing.data["code"]), (404, "account_not_found"))
        self._as(self.customer)
        self.assertEqual(self.client.get("/api/v1/audit/logs/ip-activity", {"ip": "196.190.62.22"}).status_code,
                         status.HTTP_403_FORBIDDEN)
