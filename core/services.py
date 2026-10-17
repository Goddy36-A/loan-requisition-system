import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
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
    (S.APPROVED, S.DISBURSED): {R.LOAN_OFFICER},  # COMPLETED is automatic: see record_payment
}
ALLOWED = {}
for (_frm, _to) in TRANSITIONS:
    ALLOWED.setdefault(_frm, set()).add(_to)

QUEUES = {R.LOAN_OFFICER: [S.SUBMITTED, S.DOCUMENT_REVIEW, S.INFORMATION_REQUIRED, S.APPROVED, S.DISBURSED],
          R.CREDIT_OFFICER: [S.CREDIT_ASSESSMENT], R.BRANCH_MANAGER: [S.MANAGER_REVIEW, S.DISBURSED]}
# Entering one of these states puts work in front of this staff role.
QUEUE_ENTRY = {S.SUBMITTED: R.LOAN_OFFICER, S.DOCUMENT_REVIEW: R.LOAN_OFFICER, S.APPROVED: R.LOAN_OFFICER,
               S.CREDIT_ASSESSMENT: R.CREDIT_OFFICER, S.MANAGER_REVIEW: R.BRANCH_MANAGER}
NOTIFY = {S.SUBMITTED: "Application submitted", S.INFORMATION_REQUIRED: "Additional information requested",
    S.CREDIT_ASSESSMENT: "Application in credit assessment", S.MANAGER_REVIEW: "Application at manager review",
    S.APPROVED: "Application approved", S.REJECTED: "Application rejected",
    S.DISBURSED: "Loan disbursed", S.COMPLETED: "Loan fully repaid"}

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
    if to == S.DISBURSED and not note:
        raise ValidationError("A disbursement reference is required.")
    audit_note = _override_note(app, to, note) if to in (S.APPROVED, S.REJECTED) else note
    now = timezone.now()
    if cleaned:  # created inside the transaction: it only exists if the whole transition succeeds
        Assessment.objects.create(application=app, assessor=user, **cleaned)
    if to == S.APPROVED:
        app.approved_at = now
        if app.interest_rate is None:  # applications submitted before rates were frozen
            app.interest_rate = app.loan_product.interest_rate
    if to == S.REJECTED: app.rejected_at = now
    if to == S.DISBURSED: app.disbursed_at = now
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
    if to == S.DISBURSED:
        make_schedule(app)  # due dates run from the day the money is paid out
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
    if app.schedule.exists():
        if app.payments.exists() or app.schedule.filter(paid_amount__gt=0).exists(): return  # never rewrite a schedule with payments on it
        app.schedule.all().delete()
    start = timezone.localdate(app.disbursed_at) if app.disbursed_at else timezone.localdate()
    RepaymentInstallment.objects.bulk_create([RepaymentInstallment(application=app, number=i, due_date=add_months(start, i),
        amount=pay, principal=prin, interest=intr, balance=bal)
        for i, (prin, intr, pay, bal) in enumerate(amortization(app.amount_requested, rate_of(app), app.repayment_period), 1)])

# ---- repayments --------------------------------------------------------------------------------
PAYMENT_METHODS = {m for m, _ in Payment.METHODS}

def late_fee_policy():
    return Decimal(str(settings.LATE_FEE_PERCENT)), int(settings.LATE_FEE_GRACE_DAYS)

def outstanding_total(app):
    """Everything still owed: unpaid installments plus assessed late fees. This is also the early-payoff amount
    (interest is not recalculated for early settlement)."""
    return sum((i.outstanding for i in app.schedule.all()), Decimal("0.00"))

def repayment_summary(app, today=None):
    today = today or timezone.localdate()
    rows = list(app.schedule.all())
    unpaid = [i for i in rows if i.outstanding > 0]
    overdue = [i for i in unpaid if i.due_date < today]
    return {"paid_total": sum((i.paid_amount + i.fee_paid for i in rows), Decimal("0.00")),
            "outstanding": sum((i.outstanding for i in rows), Decimal("0.00")),
            "overdue_amount": sum((i.outstanding for i in overdue), Decimal("0.00")),
            "overdue_count": len(overdue), "fees_due": sum((i.fee_due for i in rows), Decimal("0.00")),
            "next_due": unpaid[0] if unpaid else None, "installments_paid": len(rows) - len(unpaid), "installments": len(rows)}

def _message_customer(app, title, message, ntype):
    Notification.objects.create(recipient=app.customer, title=title, notification_type=ntype, message=message)
    SmsLog.objects.create(recipient=app.customer, phone=getattr(getattr(app.customer, "profile", None), "phone", ""),
                          text=f"[SIMULATED SMS] {message}"[:300])

@transaction.atomic
def assess_late_fees(app, today=None):
    """Charge the one-off late fee on installments unpaid more than the grace period after their due date.
    Idempotent: an installment is only ever assessed once. Returns the installments charged."""
    if app.status != S.DISBURSED: return []
    pct, grace = late_fee_policy()
    today, charged = today or timezone.localdate(), []
    for inst in app.schedule.select_for_update():
        if inst.fee_assessed_on or inst.amount_due <= 0 or today <= inst.due_date + timedelta(days=grace):
            continue
        fee = (inst.amount_due * pct / 100).quantize(CENT, ROUND_HALF_UP)
        inst.fee_assessed_on = today
        if fee > 0:
            inst.late_fee = fee
            audit(None, "Late fee assessed", app, f"Installment {inst.number} due {inst.due_date}: UGX {fee} ({pct}% of UGX {inst.amount_due} unpaid)")
            _message_customer(app, "Late payment fee", f"{app.application_number}: installment {inst.number} is overdue. "
                f"A late fee of UGX {fee} was added.", "LATE_FEE")
            charged.append(inst)
        inst.save()
    return charged

def assess_all_late_fees(today=None):
    return sum(len(assess_late_fees(a, today)) for a in LoanApplication.objects.filter(status=S.DISBURSED))

@transaction.atomic
def record_payment(app, amount, method, user, reference="", paid_on=None):
    """Record money received and allocate it oldest installment first (late fee, then installment).
    Payments may run ahead of the schedule (early settlement); they may not exceed what is owed.
    When nothing is left owing the loan completes automatically."""
    app = LoanApplication.objects.select_for_update().get(pk=app.pk)
    if role_of(user) != R.LOAN_OFFICER:
        raise PermissionDenied("Only loan officers record payments.")
    if app.status != S.DISBURSED:
        raise ValidationError("Payments can only be recorded on a disbursed loan.")
    try: amount = Decimal(amount).quantize(CENT, ROUND_HALF_UP)
    except Exception: raise ValidationError("Enter a valid amount.")
    if amount <= 0: raise ValidationError("Payment must be greater than zero.")
    if method not in PAYMENT_METHODS: raise ValidationError("Invalid payment method.")
    today = timezone.localdate()
    paid_on = paid_on or today
    if paid_on > today: raise ValidationError("Payment date cannot be in the future.")
    if app.disbursed_at and paid_on < timezone.localdate(app.disbursed_at):
        raise ValidationError("Payment date cannot be before the loan was disbursed.")
    assess_late_fees(app, paid_on)  # judge lateness as of the payment date, so a backdated on-time payment is not penalised
    owed = outstanding_total(app)
    if amount > owed:
        raise ValidationError(f"Payment of UGX {amount} exceeds the outstanding balance of UGX {owed}.")
    remaining, now = amount, timezone.now()
    for inst in app.schedule.select_for_update():
        if remaining <= 0: break
        fee = min(remaining, inst.fee_due); inst.fee_paid += fee; remaining -= fee
        part = min(remaining, inst.amount_due); inst.paid_amount += part; remaining -= part
        if inst.outstanding <= 0 and not inst.paid_at: inst.paid_at = now
        inst.save()
    Payment.objects.create(application=app, amount=amount, method=method, reference=reference.strip()[:60], paid_on=paid_on, recorded_by=user)
    left = outstanding_total(app)
    audit(user, "Payment recorded", app, f"UGX {amount} via {method}" + (f" ref {reference.strip()[:60]}" if reference.strip() else "") + f"; outstanding UGX {left}")
    _message_customer(app, "Payment received", f"{app.application_number}: UGX {amount} received. Outstanding: UGX {left}.", "PAYMENT")
    if left <= 0:
        app.status, app.completed_at = S.COMPLETED, now
        app.save()
        audit(user, f"{S.DISBURSED} -> {S.COMPLETED}", app, "Loan fully repaid")
        _message_customer(app, NOTIFY[S.COMPLETED], f"{app.application_number}: {NOTIFY[S.COMPLETED]}. Thank you.", S.COMPLETED)
    return app

def send_reminders(today=None):
    """Simulated SMS + notification for installments falling due within REMINDER_DAYS_BEFORE days. Sent once each."""
    today, sent = today or timezone.localdate(), 0
    window = today + timedelta(days=int(settings.REMINDER_DAYS_BEFORE))
    for inst in RepaymentInstallment.objects.filter(application__status=S.DISBURSED, reminder_sent_on__isnull=True,
                                                    due_date__gte=today, due_date__lte=window).select_related("application__customer", "application__loan_product"):
        if inst.outstanding <= 0: continue
        _message_customer(inst.application, "Payment reminder", f"{inst.application.application_number}: UGX {inst.outstanding} "
            f"is due on {inst.due_date}.", "REMINDER")
        inst.reminder_sent_on = today; inst.save(update_fields=["reminder_sent_on"]); sent += 1
    return sent
