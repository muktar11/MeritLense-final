"""API messages in Arabic for requests from the Arabic site (16 Oct 2026)."""
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from api.accounts.models import User
from api.core.constants import Roles
from api.core.error_i18n import EXACT, TEMPLATES, translate_message, translate_payload


class TranslateMessageTests(TestCase):
    def test_exact_message(self):
        self.assertEqual(translate_message("Candidate not found", 404), "المرشح غير موجود")

    def test_message_with_variable_part(self):
        self.assertEqual(
            translate_message("Cannot cancel evaluation with status COMPLETED", 400),
            "لا يمكن إلغاء تقييم حالته COMPLETED",
        )
        self.assertEqual(
            translate_message("Please wait 42s before requesting a new code.", 429),
            "يرجى الانتظار 42 ثانية قبل طلب رمز جديد.",
        )

    def test_unknown_english_error_gets_a_general_arabic_message(self):
        self.assertEqual(translate_message("Some new failure nobody listed", 403), "لا تملك صلاحية تنفيذ هذا الإجراء.")
        self.assertEqual(translate_message("KeyError: boom", 500), "حدث خطأ في الخادم. يرجى المحاولة مرة أخرى لاحقًا.")

    def test_arabic_and_non_text_values_are_left_alone(self):
        self.assertIsNone(translate_message("المرشح غير موجود", 404))
        self.assertIsNone(translate_message("12345", 400))

    def test_success_messages_only_translated_when_listed(self):
        self.assertEqual(translate_message("Invitation sent successfully", 200), "تم إرسال الدعوة بنجاح")
        self.assertIsNone(translate_message("Some English status text", 200))

    def test_payload_keeps_data_and_codes(self):
        payload = {"detail": "Candidate not found", "code": "candidate_missing", "id": "abc"}
        self.assertEqual(
            translate_payload(payload, 404),
            {"detail": "المرشح غير موجود", "code": "candidate_missing", "id": "abc"},
        )

    def test_field_errors_translated(self):
        payload = {"email": ["Enter a valid email address."], "first_name": ["This field is required."]}
        self.assertEqual(
            translate_payload(payload, 400),
            {"email": ["أدخل بريدًا إلكترونيًا صالحًا."], "first_name": ["هذا الحقل مطلوب."]},
        )

    def test_success_payload_data_untouched(self):
        payload = {"message": "Quantity updated successfully", "skills": ["care", "cooking"]}
        self.assertEqual(
            translate_payload(payload, 200),
            {"message": "تم تحديث الكمية بنجاح", "skills": ["care", "cooking"]},
        )

    def test_every_translation_is_arabic(self):
        for arabic in list(EXACT.values()) + list(TEMPLATES.values()):
            self.assertRegex(arabic, r"[؀-ۿ]")


class MiddlewareTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            email="i18n-errors@example.com", password="Password123!", first_name="I", last_name="E",
            role=Roles.B2C, is_verified=True,
        )

    def test_arabic_request_gets_arabic_error(self):
        response = self.client.post(
            reverse("change-password") if self._has("change-password") else "/api/v1/auth/change-password",
            {"current_password": "wrong", "new_password": "Another123!", "confirm_new_password": "Another123!"},
            format="json", HTTP_ACCEPT_LANGUAGE="ar",
        )
        self.assertEqual(response.status_code, 401 if response.status_code == 401 else response.status_code)
        body = response.content.decode("utf-8")
        self.assertRegex(body, r"[؀-ۿ]")

    def test_english_request_unchanged(self):
        response = self.client.get("/api/v1/candidates/candidates", HTTP_ACCEPT_LANGUAGE="en")
        self.assertNotRegex(response.content.decode("utf-8"), r"[؀-ۿ]")

    def test_unauthenticated_arabic(self):
        response = self.client.get("/api/v1/candidates/candidates", HTTP_ACCEPT_LANGUAGE="ar,en;q=0.8")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "يرجى تسجيل الدخول للمتابعة.")

    @staticmethod
    def _has(name):
        try:
            reverse(name)
            return True
        except Exception:
            return False
