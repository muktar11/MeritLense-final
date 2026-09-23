from django.test import TestCase

from api.core.constants import Countries


class CountriesConstantTests(TestCase):
    def test_all_codes_are_unique_two_letter_strings(self):
        codes = [code for code, _ in Countries.CHOICES]
        self.assertEqual(len(codes), len(set(codes)), "Countries.CHOICES has duplicate codes")
        for code in codes:
            self.assertEqual(len(code), 2, f"{code!r} is not a 2-letter ISO 3166-1 alpha-2 code")
            self.assertTrue(code.isupper(), f"{code!r} should be uppercase")

    def test_has_roughly_the_full_iso_3166_1_country_count(self):
        # ISO 3166-1 currently lists ~195 countries - a loose range check
        # to catch a transcription error that dropped/duplicated a large
        # chunk of the list, without being brittle about the exact count.
        self.assertGreater(len(Countries.CHOICES), 190)
        self.assertLess(len(Countries.CHOICES), 210)
