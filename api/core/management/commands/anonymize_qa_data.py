"""
Anonymizes a copy of production data for the QA environment.

Intended usage: on the VM, after `pg_dump meritlense_db | psql meritlense_qa_db`
restores a fresh production snapshot into the QA database, run this against
that QA database (never against production) to replace identity fields with
fake-but-realistic values before other developers get access to it.

Candidate media (passport documents, profile/verification photos, ID
documents, company registration certificates/stamps) is never copied to the
QA server's disk, so those file fields are cleared here rather than left
pointing at files that don't exist - except verification_photo, which is
replaced with a synthetic generated placeholder (see _make_placeholder_photo)
rather than cleared outright. The candidate precheck flow hard-fails ("we
couldn't find a readable reference photo on file") without *some* readable
image there, so nulling it silently broke identity-verification testing on
QA. The placeholder is generated at runtime, not a real photo of anyone.

Interview transcripts, evaluation/scoring data, and job/question templates
are intentionally left untouched - that's the realistic "production shape"
data QA exists to test against. Only identity fields and identity documents
are in scope.
"""
import io

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from faker import Faker
from PIL import Image, ImageDraw

from api.accounts.models import (
    Company,
    CompanyEmployerProfile,
    IndividualEmployerProfile,
    TeamInvitation,
    TeamMemberProfile,
    AdminProfile,
)
from api.candidates.models import Candidate

QA_DOMAIN = "qa.meritlense.test"
DEFAULT_QA_PASSWORD = "MeritLenseQA2026!"


def _make_placeholder_photo(seed):
    """A small synthetic headshot-shaped image - not a real photo of anyone.
    Just needs to be a readable image file so the identity-verification
    precheck step has something to load."""
    image = Image.new("RGB", (400, 400), color=(230, 230, 230))
    draw = ImageDraw.Draw(image)
    draw.ellipse((100, 60, 300, 260), fill=(200, 180, 160))
    draw.ellipse((120, 260, 280, 400), fill=(180, 180, 200))
    draw.text((110, 370), f"QA PLACEHOLDER #{seed}", fill=(80, 80, 80))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    return ContentFile(buffer.getvalue(), name=f"qa-placeholder-{seed}.jpg")


class Command(BaseCommand):
    help = "Anonymizes PII in the QA database (run only against meritlense_qa_db)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--password",
            default=DEFAULT_QA_PASSWORD,
            help="Password every anonymized user account is reset to.",
        )
        parser.add_argument(
            "--admin-email",
            default="qa-admin@meritlense.com",
            help="Email guaranteed to exist as a superuser after this command runs.",
        )

    def handle(self, *args, **options):
        fake = Faker()
        Faker.seed(0)
        password = options["password"]
        admin_email = options["admin_email"]

        with transaction.atomic():
            candidate_count = self._anonymize_candidates(fake)
            user_count = self._anonymize_users(fake, password)
            individual_count = self._anonymize_individual_profiles(fake)
            company_profile_count = self._anonymize_company_profiles(fake)
            company_count = self._anonymize_companies()
            team_member_count = self._anonymize_team_members(fake)
            invitation_count = self._anonymize_invitations(fake)
            self._ensure_qa_admin(admin_email, password)

        self.stdout.write(self.style.SUCCESS("QA data anonymization complete:"))
        self.stdout.write(f"  Candidates:                {candidate_count}")
        self.stdout.write(f"  Users:                     {user_count}")
        self.stdout.write(f"  Individual employer profiles: {individual_count}")
        self.stdout.write(f"  Company employer profiles:    {company_profile_count}")
        self.stdout.write(f"  Companies:                 {company_count}")
        self.stdout.write(f"  Team member profiles:      {team_member_count}")
        self.stdout.write(f"  Team invitations:          {invitation_count}")
        self.stdout.write(f"  All passwords reset to the value passed via --password.")
        self.stdout.write(f"  Guaranteed QA admin login: {admin_email}")

    def _anonymize_candidates(self, fake):
        count = 0
        for candidate in Candidate.objects.all().iterator():
            candidate.first_name = fake.first_name()
            candidate.last_name = fake.last_name()
            candidate.email = f"candidate{candidate.id}@{QA_DOMAIN}"
            candidate.passport_id = f"QA-PP-{candidate.id:08d}"
            candidate.passport_document = ""
            candidate.profile_photo = None
            candidate.verification_photo = _make_placeholder_photo(candidate.id)
            candidate.save(
                update_fields=[
                    "first_name",
                    "last_name",
                    "email",
                    "passport_id",
                    "passport_document",
                    "profile_photo",
                    "verification_photo",
                ]
            )
            count += 1
        return count

    def _anonymize_users(self, fake, password):
        User = get_user_model()
        count = 0
        for user in User.objects.all().iterator():
            user.first_name = fake.first_name()
            user.last_name = fake.last_name()
            user.email = f"user{user.id}@{QA_DOMAIN}"
            user.set_password(password)
            user.save(update_fields=["first_name", "last_name", "email", "password"])
            count += 1
        return count

    def _anonymize_individual_profiles(self, fake):
        count = 0
        for profile in IndividualEmployerProfile.objects.all().iterator():
            profile.passport_id = f"QA-B2C-{profile.id:08d}"
            profile.phone_number = fake.phone_number()[:20]
            profile.address = fake.address().replace("\n", ", ")[:255]
            profile.id_document = ""
            profile.resume_document = ""
            profile.additional_documents = None
            profile.save(
                update_fields=[
                    "passport_id",
                    "phone_number",
                    "address",
                    "id_document",
                    "resume_document",
                    "additional_documents",
                ]
            )
            count += 1
        return count

    def _anonymize_company_profiles(self, fake):
        count = 0
        for profile in CompanyEmployerProfile.objects.all().iterator():
            profile.phone_number = fake.phone_number()[:20]
            profile.company_registration_number = f"QA-REG-{profile.id:08d}"
            profile.registration_certificate = ""
            profile.resachetified_license = ""
            profile.tax_id_document = None
            profile.additional_documents = None
            profile.save(
                update_fields=[
                    "phone_number",
                    "company_registration_number",
                    "registration_certificate",
                    "resachetified_license",
                    "tax_id_document",
                    "additional_documents",
                ]
            )
            count += 1
        return count

    def _anonymize_companies(self):
        count = 0
        for company in Company.objects.all().iterator():
            company.phone_number = ""
            company.registration_number = f"QA-REG-{company.id:08d}"
            company.registration_certificate = ""
            company.tax_id_document = None
            company.stamp_image = None
            company.save(
                update_fields=[
                    "phone_number",
                    "registration_number",
                    "registration_certificate",
                    "tax_id_document",
                    "stamp_image",
                ]
            )
            count += 1
        return count

    def _anonymize_team_members(self, fake):
        count = 0
        for profile in TeamMemberProfile.objects.all().iterator():
            profile.phone_number = fake.phone_number()[:20]
            profile.save(update_fields=["phone_number"])
            count += 1
        for admin_profile in AdminProfile.objects.all().iterator():
            admin_profile.phone_number = fake.phone_number()[:20]
            admin_profile.save(update_fields=["phone_number"])
        return count

    def _anonymize_invitations(self, fake):
        count = 0
        for invite in TeamInvitation.objects.all().iterator():
            invite.first_name = fake.first_name()
            invite.last_name = fake.last_name()
            invite.email = f"invite{invite.id}@{QA_DOMAIN}"
            invite.save(update_fields=["first_name", "last_name", "email"])
            count += 1
        return count

    def _ensure_qa_admin(self, admin_email, password):
        User = get_user_model()
        admin = User.objects.filter(is_superuser=True).order_by("id").first()
        if admin is None:
            User.objects.create_superuser(
                email=admin_email, password=password, first_name="QA", last_name="Admin"
            )
            return
        admin.email = admin_email
        admin.first_name = "QA"
        admin.last_name = "Admin"
        admin.is_active = True
        admin.set_password(password)
        admin.save(update_fields=["email", "first_name", "last_name", "is_active", "password"])
