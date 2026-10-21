from decimal import Decimal
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

class Role(models.TextChoices):
    CUSTOMER = "CUSTOMER"; LOAN_OFFICER = "LOAN_OFFICER"; CREDIT_OFFICER = "CREDIT_OFFICER"
    BRANCH_MANAGER = "BRANCH_MANAGER"; SYSTEM_ADMIN = "SYSTEM_ADMIN"; RESEARCH_ANALYST = "RESEARCH_ANALYST"

class Status(models.TextChoices):
    DRAFT = "DRAFT"; SUBMITTED = "SUBMITTED"; DOCUMENT_REVIEW = "DOCUMENT_REVIEW"
    INFORMATION_REQUIRED = "INFORMATION_REQUIRED"; CREDIT_ASSESSMENT = "CREDIT_ASSESSMENT"
    MANAGER_REVIEW = "MANAGER_REVIEW"; APPROVED = "APPROVED"; REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"; DISBURSED = "DISBURSED"; COMPLETED = "COMPLETED"

RISK_LEVELS = [(x, x) for x in ("LOW", "MEDIUM", "HIGH")]
RECOMMENDATIONS = [(x, x) for x in ("RECOMMEND_APPROVAL", "RECOMMEND_REJECTION", "REQUIRE_MORE_INFORMATION")]
CREDIT_HISTORY = [(x, x) for x in ("GOOD", "FAIR", "POOR", "NONE")]
NON_NEGATIVE = [MinValueValidator(0)]

class Branch(models.Model):
    name = models.CharField(max_length=100, unique=True)
    def __str__(self): return self.name

class Profile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)
    phone = models.CharField(max_length=30, blank=True)
    nin = models.CharField("National ID number (NIN)", max_length=14, blank=True)
    def __str__(self): return f"{self.user} - {self.role}"

class LoanProduct(models.Model):
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)
    min_amount = models.DecimalField(max_digits=14, decimal_places=2)
    max_amount = models.DecimalField(max_digits=14, decimal_places=2)
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2)
    max_period_months = models.PositiveIntegerField()
    required_documents = models.CharField(max_length=300, blank=True)
    is_active = models.BooleanField(default=True)
    def __str__(self): return self.name

class LoanApplication(models.Model):
    application_number = models.CharField(max_length=20, unique=True, blank=True, null=True)
    customer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="applications")
    loan_product = models.ForeignKey(LoanProduct, on_delete=models.PROTECT)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT)
    amount_requested = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    purpose = models.CharField(max_length=300)
    repayment_period = models.PositiveIntegerField(help_text="months", validators=[MinValueValidator(1)])
    employment_status = models.CharField(max_length=50, blank=True)
    employer_name = models.CharField(max_length=100, blank=True)
    monthly_income = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=NON_NEGATIVE)
    monthly_expenses = models.DecimalField(max_digits=14, decimal_places=2, default=0, validators=NON_NEGATIVE)
    existing_debt = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="Monthly debt repayments", validators=NON_NEGATIVE)
    interest_rate = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True, editable=False,
        help_text="Annual rate frozen from the product at submission, so later product edits do not change this loan")
    guarantor_name = models.CharField(max_length=100, blank=True, default="")
    guarantor_phone = models.CharField(max_length=30, blank=True, default="")
    disbursement_account = models.ForeignKey("BankAccount", null=True, blank=True, on_delete=models.PROTECT, related_name="loans",
        help_text="Bank account the loan is paid into")
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.DRAFT, db_index=True)
    decision_reason = models.TextField(blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    disbursed_at = models.DateTimeField(null=True, blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *a, **kw):
        super().save(*a, **kw)
        if not self.application_number:  # pk is unique, so the number is too
            self.application_number = f"LN-{self.created_at.year}-{self.pk:06d}"
            super().save(update_fields=["application_number"])
    def __str__(self): return self.application_number or "new"

class Document(models.Model):
    application = models.ForeignKey(LoanApplication, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=30)
    file = models.FileField(upload_to="private/")
    status = models.CharField(max_length=15, default="UPLOADED")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    comments = models.TextField(blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

class Assessment(models.Model):
    application = models.ForeignKey(LoanApplication, on_delete=models.CASCADE, related_name="assessments")
    assessor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    risk_level = models.CharField(max_length=10, choices=RISK_LEVELS)
    recommendation = models.CharField(max_length=30, choices=RECOMMENDATIONS)
    notes = models.TextField(blank=True)
    credit_history = models.CharField(max_length=10, default="NONE", help_text="Simulated bureau result entered by officer",
        choices=CREDIT_HISTORY)
    created_at = models.DateTimeField(auto_now_add=True)

class Notification(models.Model):
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    title = models.CharField(max_length=120); message = models.TextField()
    notification_type = models.CharField(max_length=30, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

class AuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    action = models.CharField(max_length=60)
    application = models.ForeignKey(LoanApplication, null=True, blank=True, on_delete=models.SET_NULL)
    description = models.TextField(blank=True)
    timestamp = models.DateTimeField(auto_now_add=True)

class SurveyQuestion(models.Model):
    category = models.CharField(max_length=40); text = models.CharField(max_length=300)
    class Meta: unique_together = ("category", "text")

class SurveyAnswer(models.Model):
    question = models.ForeignKey(SurveyQuestion, on_delete=models.CASCADE)
    respondent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    score = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])  # 1-5 Likert
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: unique_together = ("question", "respondent")

class RepaymentInstallment(models.Model):
    application = models.ForeignKey(LoanApplication, on_delete=models.CASCADE, related_name="schedule")
    number = models.PositiveSmallIntegerField(); due_date = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    principal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    interest = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="Principal outstanding after this installment")
    paid_at = models.DateTimeField(null=True, blank=True, help_text="Set when the installment and any late fee are fully paid")
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="Paid towards the installment itself")
    late_fee = models.DecimalField(max_digits=14, decimal_places=2, default=0, help_text="Penalty charged once when overdue past the grace period")
    fee_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    fee_assessed_on = models.DateField(null=True, blank=True)
    reminder_sent_on = models.DateField(null=True, blank=True)
    class Meta: ordering = ["number"]

    @property
    def amount_due(self): return self.amount - self.paid_amount
    @property
    def fee_due(self): return self.late_fee - self.fee_paid
    @property
    def outstanding(self): return self.amount_due + self.fee_due

    def state(self, today=None):
        """PAID, OVERDUE (past due date and unpaid), PARTIAL (part paid, not yet due) or DUE. Computed, never stored."""
        today = today or timezone.localdate()
        if self.outstanding <= 0: return "PAID"
        if self.due_date < today: return "OVERDUE"
        return "PARTIAL" if self.paid_amount > 0 else "DUE"

    def days_late(self, today=None):
        today = today or timezone.localdate()
        return max(0, (today - self.due_date).days) if self.outstanding > 0 else 0

class Payment(models.Model):
    """A repayment received against a disbursed loan. Allocation to installments is done in services.record_payment."""
    METHODS = [("CASH", "Cash"), ("MOBILE_MONEY", "Mobile money (simulated)"), ("BANK", "Bank transfer")]
    application = models.ForeignKey(LoanApplication, on_delete=models.CASCADE, related_name="payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    method = models.CharField(max_length=20, choices=METHODS)
    reference = models.CharField(max_length=60, blank=True, default="")
    paid_on = models.DateField()
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["-paid_on", "-id"]
    def __str__(self): return f"{self.application_id} {self.amount} on {self.paid_on}"

class SmsLog(models.Model):  # simulated SMS: nothing is actually sent
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sms_log")
    phone = models.CharField(max_length=30, blank=True); text = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)


KIN_RELATIONSHIPS = [(x, x) for x in ("Spouse", "Parent", "Child", "Sibling", "Relative", "Friend", "Other")]
WITNESS_RELATIONSHIPS = [(x, x) for x in ("Friend", "Colleague", "Relative", "Neighbour", "Employer", "Other")]

class BankAccount(models.Model):
    """A customer's bank account. 'Removing' one archives it (is_active=False) so loan history keeps pointing at it."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="bank_accounts")
    bank_name = models.CharField(max_length=100)
    account_name = models.CharField(max_length=120)
    account_number = models.CharField(max_length=20)
    branch_name = models.CharField(max_length=100, blank=True, default="")
    is_primary = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["-is_primary", "created_at", "id"]

    @property
    def masked(self):
        return "\u2022\u2022\u2022\u2022 " + self.account_number[-4:]
    def __str__(self): return f"{self.bank_name} {self.masked}"

class NextOfKin(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="next_of_kin")
    full_name = models.CharField(max_length=120)
    relationship = models.CharField(max_length=20, choices=KIN_RELATIONSHIPS)
    phone = models.CharField(max_length=30)
    address = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["created_at", "id"]
    def __str__(self): return f"{self.full_name} ({self.relationship})"

class Witness(models.Model):
    """Someone who witnesses one specific loan application (not the applicant, not the guarantor)."""
    application = models.ForeignKey(LoanApplication, on_delete=models.CASCADE, related_name="witnesses")
    full_name = models.CharField(max_length=120)
    phone = models.CharField(max_length=30)
    nin = models.CharField(max_length=14, blank=True, default="", help_text="National ID number (optional)")
    relationship = models.CharField(max_length=20, choices=WITNESS_RELATIONSHIPS)
    address = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["created_at", "id"]
    def __str__(self): return f"{self.full_name} ({self.relationship})"


class LoginAttempt(models.Model):
    """Failed sign-in counter used for throttling. One row per key ('user-ip:name|address' or 'user:name')."""
    key = models.CharField(max_length=220, unique=True)
    failures = models.PositiveIntegerField(default=0)
    first_failure_at = models.DateTimeField()
    locked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    def __str__(self): return self.key
