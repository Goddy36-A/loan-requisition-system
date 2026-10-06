from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from .models import *

S, R = Status, Role
ALLOWED = {
    S.DRAFT: {S.SUBMITTED, S.WITHDRAWN},
    S.SUBMITTED: {S.DOCUMENT_REVIEW, S.WITHDRAWN},
    S.DOCUMENT_REVIEW: {S.INFORMATION_REQUIRED, S.CREDIT_ASSESSMENT, S.WITHDRAWN},
    S.INFORMATION_REQUIRED: {S.DOCUMENT_REVIEW, S.WITHDRAWN},
    S.CREDIT_ASSESSMENT: {S.MANAGER_REVIEW},
    S.MANAGER_REVIEW: {S.APPROVED, S.REJECTED},
    S.APPROVED: {S.COMPLETED},
}
ROLES_FOR = {
    S.SUBMITTED: {R.CUSTOMER}, S.WITHDRAWN: {R.CUSTOMER},
    S.DOCUMENT_REVIEW: {R.LOAN_OFFICER, R.CUSTOMER},  # customer = reply to info request
    S.INFORMATION_REQUIRED: {R.LOAN_OFFICER}, S.CREDIT_ASSESSMENT: {R.LOAN_OFFICER},
    S.MANAGER_REVIEW: {R.CREDIT_OFFICER}, S.APPROVED: {R.BRANCH_MANAGER},
    S.REJECTED: {R.BRANCH_MANAGER}, S.COMPLETED: {R.BRANCH_MANAGER, R.SYSTEM_ADMIN},
}
QUEUES = {R.LOAN_OFFICER: [S.SUBMITTED, S.DOCUMENT_REVIEW, S.INFORMATION_REQUIRED],
          R.CREDIT_OFFICER: [S.CREDIT_ASSESSMENT], R.BRANCH_MANAGER: [S.MANAGER_REVIEW]}
NOTIFY = {S.SUBMITTED: "Application submitted", S.INFORMATION_REQUIRED: "Additional information requested",
    S.CREDIT_ASSESSMENT: "Application in credit assessment", S.MANAGER_REVIEW: "Application at manager review",
    S.APPROVED: "Application approved", S.REJECTED: "Application rejected"}

def role_of(user):
    p = getattr(user, "profile", None)
    return p.role if p else None

def audit(user, action, app=None, desc=""):
    AuditLog.objects.create(user=user, action=action, application=app, description=desc)

@transaction.atomic
def transition(app, to, user, note=""):
    app = LoanApplication.objects.select_for_update().get(pk=app.pk)
    role = role_of(user)
    if to not in ALLOWED.get(app.status, set()):
        raise ValidationError(f"Cannot move from {app.status} to {to}.")
    if role not in ROLES_FOR.get(to, set()):
        raise PermissionDenied("Your role may not perform this transition.")
    if role == R.CUSTOMER and app.customer_id != user.id:
        raise PermissionDenied("Not your application.")
    if to == S.SUBMITTED:
        p = app.loan_product
        if not (p.min_amount <= app.amount_requested <= p.max_amount) or app.repayment_period > p.max_period_months:
            raise ValidationError("Amount or repayment period outside product limits.")
        app.submitted_at = timezone.now()
    if to == S.MANAGER_REVIEW and not app.assessments.exists():
        raise ValidationError("A credit assessment is required first.")
    if to in (S.APPROVED, S.REJECTED, S.INFORMATION_REQUIRED) and not note:
        raise ValidationError("A reason is required.")
    now = timezone.now()
    if to == S.APPROVED: app.approved_at = now
    if to == S.REJECTED: app.rejected_at = now
    if to == S.COMPLETED: app.completed_at = now
    if to in (S.APPROVED, S.REJECTED):
        app.decision_reason, app.decided_by = note, user
    old, app.status = app.status, to
    app.save()
    audit(user, f"{old} -> {to}", app, note)
    if to in NOTIFY:
        Notification.objects.create(recipient=app.customer, title=NOTIFY[to], notification_type=to,
            message=f"{app.application_number}: {note or NOTIFY[to]}")
    return app
