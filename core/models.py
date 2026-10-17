from decimal import Decimal
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

class Role(models.TextChoices):
    CUSTOMER = "CUSTOMER"; LOAN_OFFICER = "LOAN_OFFICER"; CREDIT_OFFICER = "CREDIT_OFFICER"
    BRANCH_MANAGER = "BRANCH_MANAGER"; SYSTEM_ADMIN = "SYSTEM_ADMIN"; RESEARCH_ANALYST = "RESEARCH_ANALYST"

class Status(models.TextChoices):
    DRAFT = "DRAFT"; SUBMITTED = "SUBMITTED"; DOCUMENT_REVIEW = "DOCUMENT_REVIEW"
    INFORMATION_REQUIRED = "INFORMATION_REQUIRED"; CREDIT_ASSESSMENT = "CREDIT_ASSESSMENT"
    MANAGER_REVIEW = "MANAGER_REVIEW"; APPROVED = "APPROVED"; REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"; COMPLETED = "COMPLETED"

RISK_LEVELS = [(x, x) for x in ("LOW", "MEDIUM", "HIGH")]
RECOMMENDATIONS = [(x, x) for x in ("RECOMMEND_APPROVAL", "RECOMMEND_REJECTION", "REQUIRE_MORE_INFORMATION")]
CREDIT_HISTORY = [(x, x) for x in ("GOOD", "FAIR", "POOR", "NONE")]
NON_NEGATIVE = [MinValueValidator(0)]

class Branch(models.Model):
    name = models.CharField(max_length=100, unique=True)
    def __str__(self): return self.name + " (demo)"

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
    status = models.CharField(max_length=25, choices=Status.choices, default=Status.DRAFT, db_index=True)
    decision_reason = models.TextField(blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
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
    paid_at = models.DateTimeField(null=True, blank=True)
    class Meta: ordering = ["number"]

class SmsLog(models.Model):  # simulated SMS: nothing is actually sent
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sms_log")
    phone = models.CharField(max_length=30, blank=True); text = models.CharField(max_length=300)
    created_at = models.DateTimeField(auto_now_add=True)
