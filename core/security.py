"""Safety rules that only matter once the app is hosted (see config/settings.py for LOCAL_MODE)."""
from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core.exceptions import ImproperlyConfigured
from django.db import OperationalError, ProgrammingError
from .sample_accounts import PUBLISHED_PASSWORDS

FIX = "python manage.py seed_data --reset-passwords"


def accounts_with_published_passwords():
    """Sample usernames whose current password is still the published one. (Each account is only checked against its own
    published password: password hashing is deliberately slow, so this stays at about one hash per account.)"""
    from django.contrib.auth.models import User
    return [u.username for u in User.objects.filter(username__in=PUBLISHED_PASSWORDS)
            if check_password(PUBLISHED_PASSWORDS[u.username], u.password)]


def problem_message(names):
    return (f"These accounts still use the sample passwords that are published in the README: {', '.join(names)}. Anyone can sign in as "
            f"them. Give them new passwords with:  {FIX}  (new random passwords are printed once), or set SEED_PASSWORD_<USERNAME> "
            f"variables first to choose your own.")


def assert_safe_to_serve():
    """Refuse to serve when hosted and sample passwords are still in use. No-op on this computer or before the database exists."""
    if settings.LOCAL_MODE: return
    try: names = accounts_with_published_passwords()
    except (OperationalError, ProgrammingError): return  # tables not created yet (first migrate)
    if names: raise ImproperlyConfigured(problem_message(names))
