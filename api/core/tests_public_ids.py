import shutil
import tempfile
from types import SimpleNamespace

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator

from api.accounts.models import User
from api.candidates.models import Candidate
from api.core.constants import (
    BillingInterval,
    EvaluationType,
    JOB_ROLE_SCORE_AREAS,
    Languages,
    Roles,
    candidateJobRoles,
)
from api.evaluations.models import Evaluation
from api.payments.models import Price
from api.payments.serializers import CreateSubscriptionSerializer
from api.scores.serializers import CreateScoreSetSerializer


def make_file(name="document.pdf", content=b"test-file", content_type="application/pdf"):
    return SimpleUploadedFile(name, content, content_type=content_type)


class PublicIdApiTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._media_dir = tempfile.mkdtemp(prefix="public-id-tests-")
        cls._override = override_settings(MEDIA_ROOT=cls._media_dir)
        cls._override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._override.disable()
        shutil.rmtree(cls._media_dir, ignore_errors=True)
        super().tearDownClass()

    def test_schema_uses_string_identifier_patterns_for_uuid_backed_detail_routes(self):
        schema = SchemaGenerator().get_schema(request=None, public=True)
        paths = [
            "/api/v1/evaluations/evaluations/{id}",
            "/api/v1/scores/sets/{id}",
            "/api/v1/scores/scores/{id}",
            "/api/v1/audit/logs/{id}",
            "/api/v1/payments/prices/{id}",
            "/api/v1/payments/subscriptions/{id}",
            "/api/v1/payments/payments/{id}",
            "/api/v1/payments/invoices/{id}",
        ]

        for path in paths:
            operation = schema["paths"][path]["get"]
            parameter = next(param for param in operation["parameters"] if param["name"] == "id")
            self.assertEqual(parameter["schema"]["type"], "string")
            self.assertIn("[0-9]+", parameter["schema"]["pattern"])
            self.assertIn("[0-9a-fA-F]{8}", parameter["schema"]["pattern"])

    def test_score_set_and_subscription_serializers_accept_public_ids(self):
        user = User.objects.create_user(
            email="public-id@example.com",
            password="Password123!",
            first_name="Public",
            last_name="Id",
            role=Roles.B2C,
            is_verified=True,
        )
        candidate = Candidate.objects.create(
            first_name="Jane",
            last_name="Doe",
            email="candidate@example.com",
            passport_id="PID-100",
            job_role=candidateJobRoles.NANNY,
            core_skills="communication, patience",
            preferred_language=Languages.ENGLISH,
            passport_document=make_file(),
            created_by=user,
        )
        evaluation = Evaluation.objects.create(
            candidate=candidate,
            evaluation_type=EvaluationType.INTERVIEW,
            scheduled_date=timezone.now() + timezone.timedelta(days=1),
            created_by=user,
        )
        price = Price.objects.create(
            name="Starter",
            stripe_price_id="price_test_123",
            stripe_product_id="prod_test_123",
            target_user_type="B2C",
            unit_amount="25.00",
            currency="usd",
            interval=BillingInterval.MONTHLY,
            is_active=True,
        )

        request = SimpleNamespace(user=user)
        first_area = JOB_ROLE_SCORE_AREAS[candidate.job_role][0]

        score_serializer = CreateScoreSetSerializer(
            data={
                "candidate_id": str(candidate.public_id),
                "evaluation_id": str(evaluation.public_id),
                "scores": {first_area: "85.00"},
            },
            context={"request": request},
        )
        self.assertTrue(score_serializer.is_valid(), score_serializer.errors)

        subscription_serializer = CreateSubscriptionSerializer(
            data={"price_id": str(price.public_id), "quantity": 1},
            context={"request": request},
        )
        self.assertTrue(subscription_serializer.is_valid(), subscription_serializer.errors)
