from django.core.management.base import BaseCommand
from api.accounts.models import User, AdminProfile, IndividualEmployerProfile, CompanyEmployerProfile
from api.core.constants import Roles

class Command(BaseCommand):
    help = 'Create missing profiles for existing users'

    def handle(self, *args, **options):
        admin_users = User.objects.filter(role__in=[Roles.ADMIN, Roles.SUPERADMIN])
        for user in admin_users:
            try:
                AdminProfile.objects.get(user=user)
                self.stdout.write(f"✓ Admin profile exists for {user.email}")
            except AdminProfile.DoesNotExist:
                AdminProfile.objects.create(user=user)
                self.stdout.write(f"✓ Created admin profile for {user.email}")

        b2c_users = User.objects.filter(role=Roles.B2C)
        for user in b2c_users:
            try:
                IndividualEmployerProfile.objects.get(user=user)
                self.stdout.write(f"✓ B2C profile exists for {user.email}")
            except IndividualEmployerProfile.DoesNotExist:
                self.stdout.write(f"✗ Missing B2C profile for {user.email}")

        b2b_users = User.objects.filter(role=Roles.B2B)
        for user in b2b_users:
            try:
                CompanyEmployerProfile.objects.get(user=user)
                self.stdout.write(f"✓ B2B profile exists for {user.email}")
            except CompanyEmployerProfile.DoesNotExist:
                self.stdout.write(f"✗ Missing B2B profile for {user.email}")

        self.stdout.write(self.style.SUCCESS('Profile check completed!'))