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

Payments (api.payments.Customer/Subscription/PaymentMethod) get the same
treatment as everything else, for a sharper reason than "PII hygiene":
QA is wired to *live* Stripe keys (a deliberate product decision), and the
app calls stripe.Subscription.retrieve()/modify() and stripe.Customer.modify()
using whatever stripe_subscription_id/stripe_customer_id is stored locally.
Left un-anonymized, those still point at real production Stripe objects -
so opening a billing screen on QA would pull (and in some flows, mutate) a
real customer's real live subscription. Replacing those IDs with values
that don't correspond to any real Stripe object neutralizes that entirely;
any live Stripe call against them just 404s instead of touching someone
real. See _ensure_qa_b2c_account for the one guaranteed test account this
gives a "paid" status to, entirely locally.
"""
import io

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
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
from api.contracts.constants import CURRENT_VERSIONS
from api.contracts.models import Agreement
from api.core.constants import (
    AgreementMethod,
    AgreementStatus,
    AgreementType,
    JobRoles,
    Languages,
    Nationalities,
    Roles,
    SubscriptionStatus,
)
from api.payments.models import Customer, PaymentMethod, Price, Subscription

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


def _make_placeholder_file(seed, label, extension="pdf"):
    return ContentFile(
        f"QA placeholder {label} #{seed}".encode(), name=f"qa-placeholder-{label}-{seed}.{extension}"
    )


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
        parser.add_argument(
            "--b2c-email",
            default="qa-b2c@meritlense.com",
            help="Email guaranteed to exist as a B2C account with an active (local-only) subscription.",
        )

    def handle(self, *args, **options):
        fake = Faker()
        Faker.seed(0)
        password = options["password"]
        admin_email = options["admin_email"]
        b2c_email = options["b2c_email"]

        with transaction.atomic():
            candidate_count = self._anonymize_candidates(fake)
            user_count = self._anonymize_users(fake, password)
            individual_count = self._anonymize_individual_profiles(fake)
            company_profile_count = self._anonymize_company_profiles(fake)
            company_count = self._anonymize_companies()
            team_member_count = self._anonymize_team_members(fake)
            invitation_count = self._anonymize_invitations(fake)
            customer_count, subscription_count, payment_method_count = self._anonymize_payments(fake)
            self._ensure_qa_admin(admin_email, password)
            self._ensure_qa_b2c_account(b2c_email, password)

        self.stdout.write(self.style.SUCCESS("QA data anonymization complete:"))
        self.stdout.write(f"  Candidates:                {candidate_count}")
        self.stdout.write(f"  Users:                     {user_count}")
        self.stdout.write(f"  Individual employer profiles: {individual_count}")
        self.stdout.write(f"  Company employer profiles:    {company_profile_count}")
        self.stdout.write(f"  Companies:                 {company_count}")
        self.stdout.write(f"  Team member profiles:      {team_member_count}")
        self.stdout.write(f"  Team invitations:          {invitation_count}")
        self.stdout.write(f"  Stripe customers:          {customer_count}")
        self.stdout.write(f"  Subscriptions:             {subscription_count}")
        self.stdout.write(f"  Payment methods:           {payment_method_count}")
        self.stdout.write(f"  All passwords reset to the value passed via --password.")
        self.stdout.write(f"  Guaranteed QA admin login: {admin_email}")
        self.stdout.write(f"  Guaranteed QA B2C (paid) login: {b2c_email}")

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

    def _anonymize_payments(self, fake):
        customer_count = 0
        for customer in Customer.objects.all().iterator():
            customer.email = f"customer{customer.id}@{QA_DOMAIN}"
            customer.name = fake.name()
            customer.phone = fake.phone_number()[:20]
            customer.stripe_customer_id = f"cus_qa_{customer.id:08d}"
            customer.default_payment_method_id = ""
            customer.save(
                update_fields=["email", "name", "phone", "stripe_customer_id", "default_payment_method_id"]
            )
            customer_count += 1

        subscription_count = 0
        for subscription in Subscription.objects.all().iterator():
            subscription.stripe_subscription_id = f"sub_qa_{subscription.id:08d}"
            subscription.save(update_fields=["stripe_subscription_id"])
            subscription_count += 1

        payment_method_count = 0
        for method in PaymentMethod.objects.all().iterator():
            method.stripe_payment_method_id = f"pm_qa_{method.id:08d}"
            method.billing_details = {}
            method.save(update_fields=["stripe_payment_method_id", "billing_details"])
            payment_method_count += 1

        return customer_count, subscription_count, payment_method_count

    def _ensure_qa_b2c_account(self, b2c_email, password):
        """Guarantees one B2C account with an ACTIVE subscription that isn't
        tied to any real Stripe object - it never existed there in the first
        place, unlike the anonymized-in-place rows from _anonymize_payments.
        Some billing screens that fetch live details for a specific
        subscription will error against this account (there's nothing on
        Stripe's side to fetch) - that's the accepted tradeoff for not
        touching real live billing. See the module docstring."""
        User = get_user_model()
        user, created = User.objects.get_or_create(
            email=b2c_email,
            defaults={"first_name": "QA", "last_name": "B2C", "role": Roles.B2C, "is_verified": True},
        )
        user.role = Roles.B2C
        user.is_verified = True
        user.is_active = True
        user.set_password(password)
        user.save(update_fields=["role", "is_verified", "is_active", "password"])

        IndividualEmployerProfile.objects.update_or_create(
            user=user,
            defaults={
                "passport_id": "QA-B2C-TESTACCT",
                "phone_number": "+10000000000",
                "address": "1 QA Test Street",
                "job_role": JobRoles.CHOICES[0][0],
                "nationality": Nationalities.CHOICES[0][0],
                "preferred_language": Languages.ENGLISH,
                "id_document": _make_placeholder_file("b2c-test", "id"),
                "resume_document": _make_placeholder_file("b2c-test", "resume"),
                "documents_verified": True,
                "verified_at": timezone.now(),
            },
        )

        Subscription.objects.filter(user=user, status=SubscriptionStatus.ACTIVE).delete()
        price, _ = Price.objects.update_or_create(
            stripe_price_id="price_qa_test_b2c",
            defaults={
                "name": "QA Test Plan",
                "stripe_product_id": "prod_qa_test_b2c",
                "target_user_type": "B2C",
                "unit_amount": 0,
                "feature_limits": {"candidate_limit": 100},
            },
        )
        customer, _ = Customer.objects.update_or_create(
            user=user,
            defaults={
                "stripe_customer_id": "cus_qa_test_b2c",
                "email": b2c_email,
                "name": "QA B2C",
            },
        )
        Subscription.objects.create(
            user=user,
            customer=customer,
            stripe_subscription_id=f"sub_qa_test_b2c_{timezone.now().timestamp():.0f}",
            stripe_price=price,
            status=SubscriptionStatus.ACTIVE,
            current_period_start=timezone.now(),
            current_period_end=timezone.now() + timezone.timedelta(days=365),
            current_usage={"candidate_limit": 0},
        )

        # The B2C dashboard is gated behind a signed B2C_AGREEMENT
        # (AgreementGuard, MeritLense-ui) - without this, the account looks
        # "unactivated" and gets redirected to /sign-agreements on every
        # visit, regardless of subscription status.
        Agreement.objects.filter(user=user, agreement_type=AgreementType.B2C_AGREEMENT).exclude(
            status=AgreementStatus.SIGNED
        ).update(status=AgreementStatus.SUPERSEDED)
        Agreement.objects.update_or_create(
            user=user,
            agreement_type=AgreementType.B2C_AGREEMENT,
            status=AgreementStatus.SIGNED,
            defaults={
                "version": CURRENT_VERSIONS[AgreementType.B2C_AGREEMENT],
                "method": AgreementMethod.OTP_SIGNATURE,
                "signatory_name": "QA B2C",
                "accepted_at": timezone.now(),
            },
        )
