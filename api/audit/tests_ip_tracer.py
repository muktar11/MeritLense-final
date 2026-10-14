"""Activity IP tracing: the recorded IP can't be forged through
X-Forwarded-For, every audit entry written during a request carries its
origin, sign-ins are recorded, and admins can trace an account's IPs or an
IP's accounts."""
from django.http import HttpResponse
from django.test import RequestFactory, TestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from api.accounts.models import User
from api.candidates.models import Candidate
from api.core.constants import AuditLogAction, AuditLogCategory, InterviewEvaluationTier, Roles
from api.interviews.models import InterviewConfiguration
from api.sessions.models import InterviewSession
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

    def _candidate_with_assessment(self, passport):
        candidate = Candidate.objects.create(
            first_name="Sara", last_name="Ali", email=f"{passport}@example.com", passport_id=passport, job_role="NA",
            core_skills="care", preferred_language="EN", passport_document="candidates/documents/passport/t.pdf",
            created_by=self.customer)
        config, _ = InterviewConfiguration.objects.get_or_create(
            role_code="child_caregiver", language="EN", evaluation_tier=InterviewEvaluationTier.FULL,
            defaults=dict(role_name="Child Caregiver", duration_minutes=30, total_questions=1, rubric_version="v1",
                          question_set_version="v1", is_active=True))
        session = InterviewSession.objects.create(
            candidate=candidate, config=config, role_name="Child Caregiver", role_code="child_caregiver",
            ui_language="en", candidate_language="EN", evaluation_tier=InterviewEvaluationTier.FULL,
            rubric_version="v1", question_set_version="v1", expires_at=InterviewSession.build_expiry(30),
            created_by=self.customer)
        return candidate, session

    def test_trail_shows_who_registered_and_assessed_a_candidate_and_from_where(self):
        candidate, session = self._candidate_with_assessment("TRAIL-1")
        factory = RequestFactory()
        office = factory.post("/api/v1/candidates/candidates", HTTP_X_FORWARDED_FOR="41.1.1.1")
        AuditLogService.log(user=self.customer, action=AuditLogAction.CANDIDATE_CREATED,
                            category=AuditLogCategory.CANDIDATE, description="created", resource=candidate,
                            request=office)
        AuditLogService.log(user=self.customer, action=AuditLogAction.SESSION_CREATED,
                            category=AuditLogCategory.SESSION, description="session", resource=session,
                            request=office)
        AuditLogService.log_system(action=AuditLogAction.ANSWER_SUBMITTED, category=AuditLogCategory.SESSION,
                                   description="answer", resource=session,
                                   request=factory.post("/api/v1/interviews/x/submit-response/",
                                                        HTTP_X_FORWARDED_FOR="102.2.2.2"))
        # Another candidate's activity stays out of this trail.
        other, _ = self._candidate_with_assessment("TRAIL-2")
        AuditLogService.log(user=self.customer, action=AuditLogAction.CANDIDATE_CREATED,
                            category=AuditLogCategory.CANDIDATE, description="other", resource=other, request=office)

        self._as(self.admin)
        trail = self.client.get("/api/v1/audit/logs/trail", {"passport_id": "TRAIL-1"})
        self.assertEqual(trail.status_code, 200, trail.data)
        self.assertEqual(trail.data["registration"]["ip_address"], "41.1.1.1")
        self.assertEqual(trail.data["registration"]["by_email"], self.customer.email)
        assessment = trail.data["assessments"][0]
        self.assertEqual(assessment["created_origin"]["ip_address"], "41.1.1.1")
        self.assertEqual(assessment["candidate_ips"], ["102.2.2.2"])
        self.assertEqual(trail.data["event_count"], 3)
        same = self.client.get("/api/v1/audit/logs/trail", {"session_id": str(session.public_id)})
        self.assertEqual(same.data["candidate"]["id"], str(candidate.public_id))

        events = self.client.get("/api/v1/audit/logs", {"candidate_id": str(candidate.public_id)}).data
        self.assertEqual(events["count"], 3)
        self.assertTrue(all(row["trail_candidate_id"] == str(candidate.public_id) for row in events["results"]))
        self.assertIn("Candidate", {row["resource_type_name"] for row in events["results"]})
        by_ip = self.client.get("/api/v1/audit/logs", {"candidate_id": str(candidate.public_id),
                                                       "ip_address": "102.2.2.2"}).data
        self.assertEqual([row["action"] for row in by_ip["results"]], [AuditLogAction.ANSWER_SUBMITTED])

    def test_trail_flags_a_candidate_not_added_through_the_app(self):
        self._candidate_with_assessment("SEEDED-1")  # no CANDIDATE_CREATED entry, like the 29 Sep QA fixtures
        self._as(self.admin)
        trail = self.client.get("/api/v1/audit/logs/trail", {"passport_id": "SEEDED-1"})
        self.assertIsNone(trail.data["registration"])
        missing = self.client.get("/api/v1/audit/logs/trail", {"passport_id": "NOPE"})
        self.assertEqual((missing.status_code, missing.data["code"]), (404, "candidate_not_found"))
        self._as(self.customer)
        self.assertEqual(self.client.get("/api/v1/audit/logs/trail", {"passport_id": "SEEDED-1"}).status_code,
                         status.HTTP_403_FORBIDDEN)

    def test_bad_input_and_non_admins_are_refused(self):
        self._as(self.admin)
        bad = self.client.get("/api/v1/audit/logs/ip-activity", {"ip": "nope"})
        self.assertEqual((bad.status_code, bad.data["code"]), (400, "invalid_query"))
        missing = self.client.get("/api/v1/audit/logs/ip-activity", {"email": "no@x.com"})
        self.assertEqual((missing.status_code, missing.data["code"]), (404, "account_not_found"))
        self._as(self.customer)
        self.assertEqual(self.client.get("/api/v1/audit/logs/ip-activity", {"ip": "196.190.62.22"}).status_code,
                         status.HTTP_403_FORBIDDEN)
