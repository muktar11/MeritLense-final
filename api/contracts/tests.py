from django.test import TestCase

from api.contracts.constants import CURRENT_VERSIONS
from api.contracts.pdf_service import render_preview_html
from api.core.constants import AgreementType


class FakeCompany:
    name = "Acme Staffing LLC"
    registration_number = "REG-12345"
    country = "AE"


class FakeIndividualProfile:
    nationality = "PH"


class FakeUser:
    individual_profile = FakeIndividualProfile()
    email = "jane@example.com"

    def get_full_name(self):
        return "Jane Doe"


class AgreementTemplateContentTests(TestCase):
    """B2B/B2C agreement bodies must be the real legal text, not the
    placeholder that shipped before legal review - see CURRENT_VERSIONS
    and api/contracts/templates/contracts/*.html."""

    def test_b2b_agreement_has_no_placeholder_notice(self):
        html = render_preview_html(
            AgreementType.B2B_AGREEMENT, CURRENT_VERSIONS[AgreementType.B2B_AGREEMENT],
            company=FakeCompany(), user=None,
        )
        self.assertNotIn("PLACEHOLDER", html)
        self.assertIn("Acme Staffing LLC", html)
        self.assertIn("Republic of Estonia", html)

    def test_b2c_agreement_has_no_placeholder_notice(self):
        html = render_preview_html(
            AgreementType.B2C_AGREEMENT, CURRENT_VERSIONS[AgreementType.B2C_AGREEMENT],
            company=None, user=FakeUser(),
        )
        self.assertNotIn("PLACEHOLDER", html)
        self.assertIn("Jane Doe", html)
        self.assertIn("Refund Policy (B2C)", html)

    def test_b2b_and_b2c_versions_match_provided_documents(self):
        self.assertEqual(CURRENT_VERSIONS[AgreementType.B2B_AGREEMENT], "v1.4")
        self.assertEqual(CURRENT_VERSIONS[AgreementType.B2C_AGREEMENT], "v1.5")

    def test_dpa_still_flagged_as_placeholder_pending_real_content(self):
        # Not covered by this fix - no real DPA text was supplied. Left as a
        # canary so this doesn't silently stay stale once DPA text lands.
        html = render_preview_html(
            AgreementType.DPA, CURRENT_VERSIONS[AgreementType.DPA],
            company=FakeCompany(), user=None,
        )
        self.assertIn("PLACEHOLDER", html)
