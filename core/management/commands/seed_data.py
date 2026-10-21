import os
import secrets
from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from core.models import *
from core.sample_accounts import ACCOUNTS, PUBLISHED_PASSWORDS

PRODUCTS = [("Personal Loan", 500000, 20000000, 18, 36), ("Salary Loan", 500000, 30000000, 16, 48),
    ("Business Loan", 1000000, 50000000, 20, 60), ("Education Loan", 500000, 15000000, 14, 48),
    ("Emergency Loan", 100000, 3000000, 22, 12)]
DESCRIPTIONS = {"Personal Loan": "Flexible financing for personal needs.", "Salary Loan": "An advance against your regular salary.",
    "Business Loan": "Working capital and growth financing for your business.", "Education Loan": "Tuition and other school-related costs.",
    "Emergency Loan": "Quick, short-term cash for urgent needs."}
QUESTIONS = {"System Accessibility": ["It is easy to access the online loan system.", "The system is available when I need it."],
    "System Efficiency": ["The system processes applications quickly.", "The system responds without noticeable delay."],
    "Service Delivery Quality": ["I am satisfied with the service received.", "My application was handled in good time."],
    "Accessibility of Loan Services": ["Applying for a loan online is easy.", "I can complete a loan request fully online."]}


class Command(BaseCommand):
    help = ("Create the standard users, branches, loan products and survey questions. Safe to run repeatedly. On this computer the "
            "users get the published sample passwords; when hosted they get random passwords (printed once) or SEED_PASSWORD_<USERNAME>.")
    requires_system_checks = []  # this command is the fix for the hosted-with-sample-passwords check, so it must be able to run

    def add_arguments(self, parser):
        parser.add_argument("--reset-passwords", action="store_true",
            help="Set the sample accounts' passwords again (the published ones on this computer, new random ones when hosted).")

    def password_for(self, name):
        """(password, generated?) for a sample account under the current mode."""
        if settings.LOCAL_MODE: return PUBLISHED_PASSWORDS[name], False
        chosen = os.environ.get(f"SEED_PASSWORD_{name.upper()}")
        if chosen:
            if chosen == PUBLISHED_PASSWORDS[name]:
                raise CommandError(f"SEED_PASSWORD_{name.upper()} is the published sample password. Choose a different one.")
            return chosen, False
        return secrets.token_urlsafe(12), True

    def handle(self, *a, reset_passwords=False, **kw):
        issued = []
        for name, _, role in ACCOUNTS:
            u, created = User.objects.get_or_create(username=name)
            if created or reset_passwords:
                pw, generated = self.password_for(name)
                u.set_password(pw)
                if generated: issued.append((name, pw))
            if name == "admin": u.is_staff = u.is_superuser = True
            u.save(); Profile.objects.get_or_create(user=u, defaults={"role": role})
            if name == "customer1":  # sample customer, ready to apply straight away
                Profile.objects.filter(user=u).update(nin="CM12345678ABCD", phone="+256700000001")
                BankAccount.objects.get_or_create(user=u, bank_name="Centenary Bank", account_number="3100012345678",
                    defaults=dict(account_name="Customer One", branch_name="Kampala Road", is_primary=True))
                NextOfKin.objects.get_or_create(user=u, full_name="Grace Kin", defaults=dict(relationship="Sibling", phone="+256700000002"))
        for b in ["Kampala Main Branch", "Ntinda Branch", "Jinja Branch", "Mbarara Branch", "Entebbe Branch"]:
            Branch.objects.get_or_create(name=b)
        for n, lo, hi, rate, per in PRODUCTS:
            LoanProduct.objects.get_or_create(name=n, defaults=dict(min_amount=lo, max_amount=hi, interest_rate=rate,
                max_period_months=per, description=DESCRIPTIONS[n], required_documents="National ID, Payslip"))
        for n, d in DESCRIPTIONS.items():  # replace the placeholder text older databases were seeded with
            LoanProduct.objects.filter(name=n, description="Fictional research product").update(description=d)
        for c, qs in QUESTIONS.items():
            for t in qs: SurveyQuestion.objects.get_or_create(category=c, text=t)
        self.stdout.write("Seed complete.")
        if issued:
            self.stdout.write("\nNew passwords (shown once, not stored anywhere else; change them after signing in):")
            for name, pw in issued: self.stdout.write(f"  {name:<14} {pw}")
        elif reset_passwords and settings.LOCAL_MODE:
            self.stdout.write("Sample passwords were reset to the published ones.")
