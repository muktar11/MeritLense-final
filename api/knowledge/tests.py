from django.test import TestCase
from rest_framework.test import APIClient

from api.accounts.models import User
from api.audit.models import AuditLog
from api.core.constants import AuditLogAction, Roles

from .models import KnowledgeEntry
from .services import KnowledgeContentService


def make_entry(**overrides):
    defaults = dict(
        question_id="ML-QA-TEST-001",
        category="Test Category",
        category_ar="فئة اختبارية",
        category_id="test-category",
        category_order=0,
        sequence=1,
        question_en="What is this?",
        question_ar="ما هذا؟",
        short_answer_en="A test entry.",
        short_answer_ar="مدخل اختباري.",
        status=KnowledgeEntry.Status.APPROVED,
        version="v1.0",
        visibility=KnowledgeEntry.Visibility.PUBLIC,
        content_classification=KnowledgeEntry.ContentClassification.PUBLIC,
        sensitive_content=False,
        ai_retrieval_allowed=True,
    )
    defaults.update(overrides)
    return KnowledgeEntry.objects.create(**defaults)


class PublicKnowledgeBaseAccessControlTests(TestCase):
    """The public endpoint is the one place unauthenticated users reach -
    every test here is, in effect, an "unauthorized access attempt" test:
    confirming that content which should be restricted never makes it into
    the response, regardless of which axis (status/visibility) restricts it."""

    def setUp(self):
        self.client = APIClient()
        # The real Master Q&A dataset is seeded by a data migration and is
        # therefore present in the test database too (Django applies all
        # migrations, including data migrations, when building it) - clear
        # it so these tests can assert on an exact, controlled fixture set
        # instead of "contains at least" against 42 unrelated real rows.
        KnowledgeEntry.objects.all().delete()

    def test_public_endpoint_returns_only_approved_public_current_entries(self):
        visible = make_entry(question_id="ML-QA-VISIBLE")
        make_entry(question_id="ML-QA-PARTNER", visibility=KnowledgeEntry.Visibility.PARTNER)
        make_entry(question_id="ML-QA-INTERNAL", visibility=KnowledgeEntry.Visibility.INTERNAL)
        make_entry(question_id="ML-QA-AUTH", visibility=KnowledgeEntry.Visibility.AUTHENTICATED)
        make_entry(question_id="ML-QA-DRAFT", status=KnowledgeEntry.Status.DRAFT)
        make_entry(question_id="ML-QA-ARCHIVED", status=KnowledgeEntry.Status.ARCHIVED)
        make_entry(question_id="ML-QA-NOT-CURRENT", is_current=False)

        response = self.client.get("/api/v1/knowledge/faq")
        self.assertEqual(response.status_code, 200)
        item_ids = [item["id"] for cat in response.data["categories"] for item in cat["items"]]

        self.assertEqual(item_ids, [visible.question_id])

    def test_public_endpoint_never_exposes_classification_fields(self):
        make_entry()
        response = self.client.get("/api/v1/knowledge/faq")
        payload_str = str(response.data)
        for forbidden in ("content_classification", "sensitive_content", "ai_retrieval_allowed", "owner", "audience"):
            self.assertNotIn(forbidden, payload_str)

    def test_public_endpoint_falls_back_to_english_when_arabic_is_blank(self):
        # The 7 Partner-only rows in the real dataset have no Arabic
        # translation - a Public entry with a blank AR field (e.g. a fresh
        # draft not yet translated) must still render something in Arabic
        # rather than an empty string.
        make_entry(question_ar="", short_answer_ar="")
        response = self.client.get("/api/v1/knowledge/faq?language=ar")
        item = response.data["categories"][0]["items"][0]
        self.assertEqual(item["question"], "What is this?")
        self.assertEqual(item["answer"], "A test entry.")

    def test_public_endpoint_serves_arabic_when_requested_and_available(self):
        make_entry()
        response = self.client.get("/api/v1/knowledge/faq?language=ar")
        item = response.data["categories"][0]["items"][0]
        self.assertEqual(item["question"], "ما هذا؟")

    def test_category_title_is_localized_too_not_just_the_questions(self):
        # Regression: an earlier version of this endpoint always used the
        # English category name as the section title, even on the Arabic
        # page - only the individual question/answer text was translated.
        make_entry()
        response = self.client.get("/api/v1/knowledge/faq?language=ar")
        self.assertEqual(response.data["categories"][0]["title"], "فئة اختبارية")

        en_response = self.client.get("/api/v1/knowledge/faq?language=en")
        self.assertEqual(en_response.data["categories"][0]["title"], "Test Category")

    def test_category_title_falls_back_to_english_when_arabic_is_blank(self):
        make_entry(category_ar="")
        response = self.client.get("/api/v1/knowledge/faq?language=ar")
        self.assertEqual(response.data["categories"][0]["title"], "Test Category")

    def test_admin_endpoint_rejects_unauthenticated_requests(self):
        make_entry()
        list_response = self.client.get("/api/v1/knowledge/entries")
        self.assertEqual(list_response.status_code, 401)

        create_response = self.client.post("/api/v1/knowledge/entries", {"question_id": "X"}, format="json")
        self.assertEqual(create_response.status_code, 401)


class KnowledgeEntryAdminAccessControlTests(TestCase):
    """Confirms server-side enforcement on the management API: role checks
    happen in DRF permission classes on every action, not just in a
    frontend admin screen that happens not to exist yet."""

    def setUp(self):
        self.entry = make_entry()
        self.admin = User.objects.create_user(
            email="kb-admin@example.com", password="testpass123",
            first_name="KB", last_name="Admin", role=Roles.ADMIN, is_verified=True,
        )
        self.superadmin = User.objects.create_user(
            email="kb-superadmin@example.com", password="testpass123",
            first_name="KB", last_name="SuperAdmin", role=Roles.SUPERADMIN, is_verified=True,
        )
        self.b2b_user = User.objects.create_user(
            email="kb-b2b@example.com", password="testpass123",
            first_name="KB", last_name="B2B", role=Roles.B2B, is_verified=True,
        )
        self.candidate_user = User.objects.create_user(
            email="kb-candidate@example.com", password="testpass123",
            first_name="KB", last_name="Candidate", role=Roles.CANDIDATE, is_verified=True,
        )

    def _client_for(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_non_admin_authenticated_users_are_rejected(self):
        for user in (self.b2b_user, self.candidate_user):
            client = self._client_for(user)
            list_response = client.get("/api/v1/knowledge/entries")
            self.assertEqual(list_response.status_code, 403, user.role)

            detail_response = client.get(f"/api/v1/knowledge/entries/{self.entry.public_id}")
            self.assertEqual(detail_response.status_code, 403, user.role)

            create_response = client.post(
                "/api/v1/knowledge/entries",
                {
                    "question_id": "ML-QA-DENIED",
                    "category": "X", "category_id": "x",
                    "question_en": "q", "short_answer_en": "a",
                    "status": KnowledgeEntry.Status.APPROVED,
                    "visibility": KnowledgeEntry.Visibility.PUBLIC,
                    "content_classification": KnowledgeEntry.ContentClassification.PUBLIC,
                },
                format="json",
            )
            self.assertEqual(create_response.status_code, 403, user.role)
            self.assertFalse(KnowledgeEntry.objects.filter(question_id="ML-QA-DENIED").exists())

    def test_admin_and_superadmin_can_manage_entries(self):
        for user in (self.admin, self.superadmin):
            client = self._client_for(user)
            response = client.get("/api/v1/knowledge/entries")
            self.assertEqual(response.status_code, 200, user.role)

    def test_updating_an_entry_creates_a_new_version_and_preserves_history(self):
        client = self._client_for(self.admin)
        response = client.patch(
            f"/api/v1/knowledge/entries/{self.entry.public_id}",
            {"short_answer_en": "An updated test entry.", "version": "v1.1"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)

        self.entry.refresh_from_db()
        self.assertFalse(self.entry.is_current)
        self.assertEqual(self.entry.short_answer_en, "A test entry.")  # untouched historical row

        current = KnowledgeEntry.objects.get(question_id=self.entry.question_id, is_current=True)
        self.assertEqual(current.short_answer_en, "An updated test entry.")
        self.assertEqual(current.version, "v1.1")
        self.assertEqual(current.supersedes_id, self.entry.id)
        # Fields not part of this PATCH were carried forward from the previous version.
        self.assertEqual(current.visibility, KnowledgeEntry.Visibility.PUBLIC)
        self.assertEqual(current.question_en, self.entry.question_en)

    def test_question_id_cannot_be_changed_via_update(self):
        client = self._client_for(self.admin)
        client.patch(
            f"/api/v1/knowledge/entries/{self.entry.public_id}",
            {"question_id": "ML-QA-HIJACKED"},
            format="json",
        )
        self.assertFalse(KnowledgeEntry.objects.filter(question_id="ML-QA-HIJACKED").exists())
        current = KnowledgeEntry.objects.get(question_id=self.entry.question_id, is_current=True)
        self.assertIsNotNone(current)

    def test_archiving_soft_removes_and_keeps_history(self):
        client = self._client_for(self.admin)
        response = client.delete(f"/api/v1/knowledge/entries/{self.entry.public_id}")
        self.assertEqual(response.status_code, 204)

        self.entry.refresh_from_db()
        self.assertEqual(self.entry.status, KnowledgeEntry.Status.ARCHIVED)
        self.assertFalse(self.entry.is_current)
        # Still in the database - a hard delete would destroy the audit trail.
        self.assertTrue(KnowledgeEntry.objects.filter(pk=self.entry.pk).exists())

    def test_archived_entry_never_reaches_the_public_endpoint(self):
        client = self._client_for(self.admin)
        client.delete(f"/api/v1/knowledge/entries/{self.entry.public_id}")

        public_client = APIClient()
        response = public_client.get("/api/v1/knowledge/faq")
        item_ids = [item["id"] for cat in response.data["categories"] for item in cat["items"]]
        self.assertNotIn(self.entry.question_id, item_ids)


class KnowledgeAuditTrailTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            email="kb-audit-admin@example.com", password="testpass123",
            first_name="KB", last_name="Audit", role=Roles.ADMIN, is_verified=True,
        )

    def test_create_update_and_archive_are_all_audit_logged(self):
        created = KnowledgeContentService.create_new_version(
            actor=self.admin,
            question_id="ML-QA-AUDIT",
            category="Test", category_id="test", question_en="q", short_answer_en="a",
            status=KnowledgeEntry.Status.APPROVED, visibility=KnowledgeEntry.Visibility.PUBLIC,
            content_classification=KnowledgeEntry.ContentClassification.PUBLIC,
        )
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLogAction.KNOWLEDGE_ENTRY_CREATED, user=self.admin).exists()
        )

        updated = KnowledgeContentService.create_new_version(
            actor=self.admin, previous=created, short_answer_en="updated",
        )
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLogAction.KNOWLEDGE_ENTRY_UPDATED, user=self.admin).exists()
        )

        KnowledgeContentService.archive(updated, actor=self.admin)
        self.assertTrue(
            AuditLog.objects.filter(action=AuditLogAction.KNOWLEDGE_ENTRY_ARCHIVED, user=self.admin).exists()
        )


class KnowledgeEntryCandidateDataIsolationTests(TestCase):
    """Section 5 of the security spec requires the Knowledge Base be kept
    separate from Candidate Assessment Data. This is enforced structurally
    here, not by a runtime filter: KnowledgeEntry has no relationship to
    Candidate/Evaluation/session data at all, so there is no field path
    through which candidate PII could ever appear in a knowledge response."""

    def test_model_has_no_relationship_to_candidate_data(self):
        related_model_names = {
            field.related_model.__name__
            for field in KnowledgeEntry._meta.get_fields()
            if field.is_relation and field.related_model is not None
        }
        self.assertEqual(related_model_names, {"KnowledgeEntry"})
