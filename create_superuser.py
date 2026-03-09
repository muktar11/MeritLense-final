import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'meritlense.settings')
django.setup()

from django.contrib.auth import get_user_model
from api.core.constants import Roles, DocumentStatus

User = get_user_model()

email = os.getenv("DJANGO_SUPERUSER_EMAIL", "admin@meritlense.com")
password = os.getenv("DJANGO_SUPERUSER_PASSWORD", "Admin@123")
first_name = os.getenv("DJANGO_SUPERUSER_FIRST_NAME", "Super")
last_name = os.getenv("DJANGO_SUPERUSER_LAST_NAME", "Admin")

full_name = os.getenv("DJANGO_SUPERUSER_FULL_NAME")
if full_name and not first_name and not last_name:
    name_parts = full_name.split(' ', 1)
    first_name = name_parts[0]
    last_name = name_parts[1] if len(name_parts) > 1 else ""

def create_superuser():
    
    if not email or not password:
        print("Error: Email and password are required")
        print("Set DJANGO_SUPERUSER_EMAIL and DJANGO_SUPERUSER_PASSWORD environment variables")
        sys.exit(1)
    
    if User.objects.filter(email=email).exists():
        existing_user = User.objects.get(email=email)
        print(f"Superuser {email} already exists")
        print(f"   Role: {existing_user.role}")
        print(f"   Name: {existing_user.get_full_name()}")
        print(f"   Staff: {existing_user.is_staff}")
        print(f"   Superuser: {existing_user.is_superuser}")
        return
    
    try:
        user = User.objects.create_superuser(
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
            role=Roles.SUPERADMIN,
            is_staff=True,
            is_superuser=True,
            is_verified=True,
            documents_verification_status=DocumentStatus.APPROVED
        )
        
        print(f"Superuser created successfully!")
        print(f"   Email: {user.email}")
        print(f"   Name: {user.get_full_name()}")
        print(f"   Role: {user.role}")
        print(f"   Staff: {user.is_staff}")
        print(f"   Superuser: {user.is_superuser}")
        
        try:
            from api.accounts.models import AdminProfile
            AdminProfile.objects.get_or_create(
                user=user,
                defaults={
                    'department': 'Administration',
                    'phone_number': ''
                }
            )
            print(f"   Admin profile: Created")
        except ImportError:
            print(f"   Note: AdminProfile model not available")
        except Exception as e:
            print(f"   Warning: Could not create admin profile: {e}")
            
    except Exception as e:
        print(f"Error creating superuser: {e}")
        sys.exit(1)

def create_admin_user(email=None, password=None, first_name=None, last_name=None):
    
    if not email or not password:
        print("Error: Email and password are required for admin user")
        return None
    
    if User.objects.filter(email=email).exists():
        print(f"Admin user {email} already exists")
        return User.objects.get(email=email)
    
    try:
        user = User.objects.create_user(
            email=email,
            password=password,
            first_name=first_name or "Admin",
            last_name=last_name or "User",
            role=Roles.ADMIN,
            is_staff=True,
            is_superuser=False,
            is_verified=True,
            documents_verification_status=DocumentStatus.APPROVED
        )
        
        print(f"Admin user created successfully: {user.email}")
        
        try:
            from api.accounts.models import AdminProfile
            AdminProfile.objects.get_or_create(
                user=user,
                defaults={
                    'department': 'Administration',
                    'phone_number': ''
                }
            )
        except ImportError:
            pass
            
        return user
        
    except Exception as e:
        print(f"Error creating admin user: {e}")
        return None

if __name__ == "__main__":
    print("=" * 50)
    print("Meritlense Superuser Creation Script")
    print("=" * 50)
    
    if os.getenv("CREATE_ADMIN", "").lower() == "true":
        admin_email = os.getenv("ADMIN_EMAIL", "admin@meritlense.com")
        admin_password = os.getenv("ADMIN_PASSWORD", "Admin@123")
        admin_first = os.getenv("ADMIN_FIRST_NAME", "Admin")
        admin_last = os.getenv("ADMIN_LAST_NAME", "User")
        create_admin_user(admin_email, admin_password, admin_first, admin_last)
    else:
        create_superuser()
    
    print("=" * 50)