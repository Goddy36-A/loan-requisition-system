from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from core.models import *

USERS = [("admin", "Admin@12345", Role.SYSTEM_ADMIN), ("customer1", "Customer@12345", Role.CUSTOMER),
    ("loanofficer", "LoanOfficer@12345", Role.LOAN_OFFICER), ("creditofficer", "CreditOfficer@12345", Role.CREDIT_OFFICER),
    ("manager", "Manager@12345", Role.BRANCH_MANAGER), ("researcher", "Researcher@12345", Role.RESEARCH_ANALYST)]
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
    help = "Create the standard users, branches, loan products and survey questions. Safe to run repeatedly."
    def handle(self, *a, **kw):
        for name, pw, role in USERS:
            u, created = User.objects.get_or_create(username=name)
            if created:
                u.set_password(pw)
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
