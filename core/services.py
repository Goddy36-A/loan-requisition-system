import calendar
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from .models import *

S, R = Status, Role
CENT = Decimal("0.01")

# Who may perform each transition. Keyed by (from, to) so a role cannot reach a state
# through a path that was only meant for someone else.
TRANSITIONS = {
    (S.DRAFT, S.SUBMITTED): {R.CUSTOMER},
    (S.DRAFT, S.WITHDRAWN): {R.CUSTOMER},
    (S.SUBMITTED, S.DOCUMENT_REVIEW): {R.LOAN_OFFICER},
    (S.SUBMITTED, S.WITHDRAWN): {R.CUSTOMER},
    (S.DOCUMENT_REVIEW, S.INFORMATION_REQUIRED): {R.LOAN_OFFICER},
    (S.DOCUMENT_REVIEW, S.CREDIT_ASSESSMENT): {R.LOAN_OFFICER},
    (S.DOCUMENT_REVIEW, S.WITHDRAWN): {R.CUSTOMER},
    (S.INFORMATION_REQUIRED, S.DOCUMENT_REVIEW): {R.CUSTOMER, R.LOAN_OFFICER},  # customer = reply to info request
    (S.INFORMATION_REQUIRED, S.WITHDRAWN): {R.CUSTOMER},
    (S.CREDIT_ASSESSMENT, S.MANAGER_REVIEW): {R.CREDIT_OFFICER},
    (S.MANAGER_REVIEW, S.APPROVED): {R.BRANCH_MANAGER},
    (S.MANAGER_REVIEW, S.REJECTED): {R.BRANCH_MANAGER},
    (S.APPROVED, S.COMPLETED): {R.BRANCH_MANAGER, R.SYSTEM_ADMIN},
}
ALLOWED = {}
for (_frm, _to) in TRANSITIONS:
    ALLOWED.setdefault(_frm, set()).add(_to)

QUEUES = {R.LOAN_OFFICER: [S.SUBMITTED, S.DOCUMENT_REVIEW, S.INFORMATION_REQUIRED],
          R.CREDIT_OFFICER: [S.CREDIT_ASSESSMENT], R.BRANCH_MANAGER: [S.MANAGER_REVIEW]}
# Entering one of these states puts work in front of this staff role.
QUEUE_ENTRY = {S.SUBMITTED: R.LOAN_OFFICER, S.DOCUMENT_REVIEW: R.LOAN_OFFICER,
               S.CREDIT_ASSESSMENT: R.CREDIT_OFFICER, S.MANAGER_REVIEW: R.BRANCH_MANAGER}
NOTIFY = {S.SUBMITTED: "Application submitted", S.INFORMATION_REQUIRED: "Additional information requested",
    S.CREDIT_ASSESSMENT: "Application in credit assessment", S.MANAGER_REVIEW: "Application at manager review",
    S.APPROVED: "Application approved", S.REJECTED: "Application rejected"}

def role_of(user):
    p = getattr(user, "profile", None)
    return p.role if p else None

def next_options(app, role):
    """Statuses this role may move the application to right now (drives the action dropdown)."""
    return sorted(to for (frm, to), roles in TRANSITIONS.items() if frm == app.status and role in roles)

def audit(user, action, app=None, desc=""):
    AuditLog.objects.create(user=user, action=action, application=app, description=desc)

# ---- documents ---------------------------------------------------------------------------------
def latest_documents(app):
    """Most recent upload per document type; earlier uploads of the same type are superseded."""
    latest = {}
    for d in app.documents.order_by("uploaded_at", "pk"):
        latest[d.doc_type.strip().lower()] = d
    return latest

def required_types(product):
    return [t.strip() for t in product.required_documents.split(",") if t.strip()]

def document_problems(app):
    """Reasons the application cannot go to credit assessment yet (empty list = ready)."""
    latest = latest_documents(app)
    if not latest:
        return ["At least one document must be uploaded."]
    problems = [f"Required document missing: {t}." for t in required_types(app.loan_product) if t.lower() not in latest]
    unverified = sorted(d.doc_type for d in latest.values() if d.status != "VERIFIED")
    if unverified:
        problems.append("Not yet verified: " + ", ".join(unverified) + ".")
    return problems

# ---- credit assessment -------------------------------------------------------------------------
def clean_assessment(data):
    """Validate the credit officer's input against the model's choices; returns kwargs for Assessment."""
    out = {"risk_level": (data.get("risk_level") or "").strip(), "recommendation": (data.get("recommendation") or "").strip(),
           "credit_history": (data.get("credit_history") or "NONE").strip(), "notes": (data.get("notes") or "").strip()}
    for field, choices in (("risk_level", RISK_LEVELS), ("recommendation", RECOMMENDATIONS), ("credit_history", CREDIT_HISTORY)):
        if out[field] not in {c[0] for c in choices}:
            raise ValidationError(f"Invalid {field.replace('_', ' ')}.")
    return out

def _override_note(app, to, note):
    """Prefix the audit note when a manager decides against the credit officer's recommendation."""
    last = app.assessments.order_by("-created_at", "-pk").first()
    if last and ((to == S.APPROVED and last.recommendation != "RECOMMEND_APPROVAL") or
                 (to == S.REJECTED and last.recommendation == "RECOMMEND_APPROVAL")):
        return f"[OVERRIDE of credit recommendation {last.recommendation}, risk {last.risk_level}] {note}"
    return note

def _notify_staff(app, to, actor_role):
    staff_role = QUEUE_ENTRY.get(to)
    if not staff_role or staff_role == actor_role:
        return
    users = get_user_model().objects.filter(profile__role=staff_role, is_active=True)
    Notification.objects.bulk_create([Notification(recipient=u, title="New item in your queue", notification_type=to,
        message=f"{app.application_number}: now {to}") for u in users])

@transaction.atomic
def transition(app, to, user, note="", assessment=None):
    app = LoanApplication.objects.select_for_update().get(pk=app.pk)
    role = role_of(user)
    roles = TRANSITIONS.get((app.status, to))
    if roles is None:
        raise ValidationError(f"Cannot move from {app.status} to {to}.")
    if role not in roles:
        raise PermissionDenied("Your role may not perform this transition.")
    if role == R.CUSTOMER and app.customer_id != user.id:
        raise PermissionDenied("Not your application.")
    if to == S.SUBMITTED:
        p = app.loan_product
        if not (p.min_amount <= app.amount_requested <= p.max_amount) or not (1 <= app.repayment_period <= p.max_period_months):
            raise ValidationError("Amount or repayment period outside product limits.")
        pr = getattr(app.customer, "profile", None)
        if not (pr and pr.nin and pr.phone):
            raise ValidationError("Complete your profile (NIN and phone) before submitting.")
        app.submitted_at = timezone.now()
        app.interest_rate = p.interest_rate  # freeze the rate this customer applied under
    if to == S.CREDIT_ASSESSMENT:
        problems = document_problems(app)
        if problems:
            raise ValidationError(problems)
    cleaned = None
    if to == S.MANAGER_REVIEW:
        if not assessment:
            raise ValidationError("A credit assessment is required.")
        cleaned = clean_assessment(assessment)
    if to in (S.APPROVED, S.REJECTED, S.INFORMATION_REQUIRED) and not note:
        raise ValidationError("A reason is required.")
    audit_note = _override_note(app, to, note) if to in (S.APPROVED, S.REJECTED) else note
    now = timezone.now()
    if cleaned:  # created inside the transaction: it only exists if the whole transition succeeds
        Assessment.objects.create(application=app, assessor=user, **cleaned)
    if to == S.APPROVED:
        app.approved_at = now
        if app.interest_rate is None:  # applications submitted before rates were frozen
            app.interest_rate = app.loan_product.interest_rate
    if to == S.REJECTED: app.rejected_at = now
    if to == S.COMPLETED: app.completed_at = now
    if to in (S.APPROVED, S.REJECTED):
        app.decision_reason, app.decided_by = note, user
    old, app.status = app.status, to
    app.save()
    audit(user, f"{old} -> {to}", app, audit_note)
    if to in NOTIFY:
        Notification.objects.create(recipient=app.customer, title=NOTIFY[to], notification_type=to,
            message=f"{app.application_number}: {note or NOTIFY[to]}")
        SmsLog.objects.create(recipient=app.customer, phone=getattr(getattr(app.customer, "profile", None), "phone", ""),
            text=f"[SIMULATED SMS] {app.application_number}: {NOTIFY[to]}")
    _notify_staff(app, to, role)
    if to == S.APPROVED:
        make_schedule(app)
    return app

# ---- money -------------------------------------------------------------------------------------
def rate_of(app):
    """Annual rate for this application: the frozen one if set, else the product's current rate."""
    return app.interest_rate if app.interest_rate is not None else app.loan_product.interest_rate

def installment(amount, annual_rate, months):
    """Standard amortised monthly installment (simplified: no fees)."""
    amount, months = Decimal(amount), int(months)
    if months < 1:
        return Decimal("0.00")
    r = Decimal(annual_rate) / Decimal(1200)
    if r == 0: return (amount / months).quantize(CENT, ROUND_HALF_UP)
    f = (1 + r) ** months
    return (amount * r * f / (f - 1)).quantize(CENT, ROUND_HALF_UP)

def amortization(amount, annual_rate, months):
    """Rows of (principal, interest, payment, balance). The last payment clears the balance exactly,
    so the total repaid always equals principal + interest with no rounding leftovers."""
    amount, months = Decimal(amount), int(months)
    if months < 1:
        return []
    r, inst, bal, rows = Decimal(annual_rate) / Decimal(1200), installment(amount, annual_rate, months), amount, []
    for i in range(1, months + 1):
        interest = (bal * r).quantize(CENT, ROUND_HALF_UP)
        principal, pay = (bal, bal + interest) if i == months else (inst - interest, inst)
        bal = (bal - principal).quantize(CENT)
        rows.append((principal, interest, pay, bal))
    return rows

def affordability(app):
    """Simplified check: installment must fit within income - expenses - existing monthly debt."""
    inst = installment(app.amount_requested, rate_of(app), app.repayment_period)
    disposable = app.monthly_income - app.monthly_expenses - app.existing_debt
    return {"installment": inst, "disposable": disposable, "affordable": inst > 0 and disposable > 0 and inst <= disposable}

def add_months(d, n):
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))

def make_schedule(app):
    if app.schedule.exists(): return
    start = timezone.localdate()
    RepaymentInstallment.objects.bulk_create([RepaymentInstallment(application=app, number=i, due_date=add_months(start, i),
        amount=pay, principal=prin, interest=intr, balance=bal)
        for i, (prin, intr, pay, bal) in enumerate(amortization(app.amount_requested, rate_of(app), app.repayment_period), 1)])
