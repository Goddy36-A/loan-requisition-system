"""The sample sign-ins created by `seed_data`. Their passwords are published (README, setup output, login page), so they are
only ever used when the app runs on this computer; see core/security.py for what happens when it is hosted."""
from .models import Role

ACCOUNTS = [("admin", "Admin@12345", Role.SYSTEM_ADMIN), ("customer1", "Customer@12345", Role.CUSTOMER),
            ("loanofficer", "LoanOfficer@12345", Role.LOAN_OFFICER), ("creditofficer", "CreditOfficer@12345", Role.CREDIT_OFFICER),
            ("manager", "Manager@12345", Role.BRANCH_MANAGER), ("researcher", "Researcher@12345", Role.RESEARCH_ANALYST)]
PUBLISHED_PASSWORDS = {name: pw for name, pw, _ in ACCOUNTS}


def existing_accounts():
    """(username, password, role label) for the sample accounts that exist in this database."""
    from django.contrib.auth.models import User
    have = set(User.objects.filter(username__in=PUBLISHED_PASSWORDS).values_list("username", flat=True))
    return [(n, pw, Role(role).label) for n, pw, role in ACCOUNTS if n in have]
