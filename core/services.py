import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from .models import *
from .validators import normalise_phone

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

# ---- customer account: bank accounts, next of kin, witnesses ---------------------------------------
MAX_BANK_ACCOUNTS, MAX_NEXT_OF_KIN = 5, 5
EDITABLE = (S.DRAFT, S.INFORMATION_REQUIRED)  # statuses in which the customer may still change an application
IN_FLIGHT = [S.SUBMITTED, S.DOCUMENT_REVIEW, S.INFORMATION_REQUIRED, S.CREDIT_ASSESSMENT, S.MANAGER_REVIEW, S.APPROVED, S.DISBURSED]

def witness_limits():
    return int(getattr(settings, "MIN_WITNESSES", 1)), int(getattr(settings, "MAX_WITNESSES", 3))

def active_accounts(user):
    return user.bank_accounts.filter(is_active=True)

def primary_account(user):
    accounts = active_accounts(user)
    return accounts.filter(is_primary=True).first() or accounts.first()

def effective_account(app):
    """The account the loan will be paid into: the one chosen on the application, else the customer's primary."""
    a = app.disbursement_account
    return a if a and a.is_active and a.user_id == app.customer_id else primary_account(app.customer)

def account_checklist(user):
    """(label, done, message-if-missing) for everything a customer needs on their account before applying."""
    pr = getattr(user, "profile", None)
    return [("Phone number", bool(pr and pr.phone), "Add your phone number."),
            ("National ID number (NIN)", bool(pr and pr.nin), "Add your National ID number (NIN)."),
            ("Bank account", active_accounts(user).exists(), "Add a bank account to receive the loan."),
            ("Next of kin", user.next_of_kin.exists(), "Add at least one next of kin.")]

def account_readiness(user):
    return [msg for _, ok, msg in account_checklist(user) if not ok]

def submission_problems(app):
    """Everything still missing before this application can be submitted (empty list = ready)."""
    p, problems = app.loan_product, []
    if not (p.min_amount <= app.amount_requested <= p.max_amount) or not (1 <= app.repayment_period <= p.max_period_months):
        problems.append("Amount or repayment period outside product limits.")
    problems += account_readiness(app.customer)
    need, _ = witness_limits()
    if app.witnesses.count() < need:
        problems.append(f"Add at least {need} witness{'' if need == 1 else 'es'} to this application.")
    return problems

def _require_customer(user):
    if role_of(user) != R.CUSTOMER: raise PermissionDenied("Customers only.")

@transaction.atomic
def add_bank_account(user, data):
    _require_customer(user)
    if active_accounts(user).count() >= MAX_BANK_ACCOUNTS:
        raise ValidationError(f"You can keep at most {MAX_BANK_ACCOUNTS} bank accounts. Remove one first.")
    if active_accounts(user).filter(bank_name__iexact=data["bank_name"], account_number=data["account_number"]).exists():
        raise ValidationError("This bank account is already on your profile.")
    acct = BankAccount.objects.create(user=user, bank_name=data["bank_name"].strip(), account_name=data["account_name"].strip(),
        account_number=data["account_number"], branch_name=(data.get("branch_name") or "").strip(),
        is_primary=not active_accounts(user).exists())  # the first account is the primary one
    audit(user, "Bank account added", None, f"{acct.bank_name} {acct.masked}")
    return acct

@transaction.atomic
def set_primary_account(user, account):
    _require_customer(user)
    if account.user_id != user.id or not account.is_active: raise PermissionDenied
    active_accounts(user).exclude(pk=account.pk).update(is_primary=False)
    account.is_primary = True; account.save(update_fields=["is_primary"])
    audit(user, "Primary bank account changed", None, f"{account.bank_name} {account.masked}")

@transaction.atomic
def remove_bank_account(user, account):
    """Archive an account. Refused while a loan that uses it is in progress or being repaid."""
    _require_customer(user)
    if account.user_id != user.id or not account.is_active: raise PermissionDenied
    if LoanApplication.objects.filter(disbursement_account=account, status__in=IN_FLIGHT).exists():
        raise ValidationError("This account is linked to a loan that is in progress or being repaid, so it cannot be removed yet.")
    LoanApplication.objects.filter(disbursement_account=account, status=S.DRAFT).update(disbursement_account=None)
    was_primary = account.is_primary
    account.is_active = account.is_primary = False; account.save(update_fields=["is_active", "is_primary"])
    if was_primary:
        nxt = active_accounts(user).first()
        if nxt: nxt.is_primary = True; nxt.save(update_fields=["is_primary"])
    audit(user, "Bank account removed", None, f"{account.bank_name} {account.masked}")

@transaction.atomic
def add_next_of_kin(user, data):
    _require_customer(user)
    if user.next_of_kin.count() >= MAX_NEXT_OF_KIN:
        raise ValidationError(f"You can list at most {MAX_NEXT_OF_KIN} next of kin.")
    kin = NextOfKin.objects.create(user=user, full_name=data["full_name"].strip(), relationship=data["relationship"],
        phone=normalise_phone(data["phone"]), address=(data.get("address") or "").strip())
    audit(user, "Next of kin added", None, f"{kin.full_name} ({kin.relationship})")
    return kin

@transaction.atomic
def remove_next_of_kin(user, kin):
    _require_customer(user)
    if kin.user_id != user.id: raise PermissionDenied
    if user.next_of_kin.count() <= 1 and LoanApplication.objects.filter(customer=user, status__in=IN_FLIGHT).exists():
        raise ValidationError("You need at least one next of kin while a loan is in progress. Add another before removing this one.")
    audit(user, "Next of kin removed", None, f"{kin.full_name} ({kin.relationship})")
    kin.delete()

def _own_editable_application(app, user, what):
    if role_of(user) != R.CUSTOMER or app.customer_id != user.id: raise PermissionDenied
    if app.status not in EDITABLE:
        raise ValidationError(f"{what} can only be changed while the application is a draft or waiting for more information.")

@transaction.atomic
def add_witness(app, user, data):
    app = LoanApplication.objects.select_for_update().get(pk=app.pk)
    _own_editable_application(app, user, "Witnesses")
    _, most = witness_limits()
    if app.witnesses.count() >= most:
        raise ValidationError(f"An application can have at most {most} witnesses.")
    phone, nin = normalise_phone(data["phone"]), (data.get("nin") or "").strip().upper()
    pr = getattr(user, "profile", None)
    if (pr and pr.phone and normalise_phone(pr.phone) == phone) or (pr and pr.nin and nin and pr.nin.upper() == nin):
        raise ValidationError("You cannot be a witness to your own loan.")
    if app.guarantor_phone and normalise_phone(app.guarantor_phone) == phone:
        raise ValidationError("A witness cannot also be the guarantor.")
    if app.witnesses.filter(phone=phone).exists():
        raise ValidationError("This witness has already been added.")
    w = Witness.objects.create(application=app, full_name=data["full_name"].strip(), phone=phone, nin=nin,
        relationship=data["relationship"], address=(data.get("address") or "").strip())
    audit(user, "Witness added", app, f"{w.full_name} ({w.relationship})")
    return w

@transaction.atomic
def remove_witness(user, witness):
    app = LoanApplication.objects.select_for_update().get(pk=witness.application_id)  # never trust a cached copy of the status
    _own_editable_application(app, user, "Witnesses")
    audit(user, "Witness removed", app, f"{witness.full_name} ({witness.relationship})")
    witness.delete()

@transaction.atomic
def set_disbursement_account(app, user, account):
    app = LoanApplication.objects.select_for_update().get(pk=app.pk)
    _own_editable_application(app, user, "The payout account")
    if account.user_id != user.id or not account.is_active:
        raise ValidationError("Choose one of your own active bank accounts.")
    app.disbursement_account = account; app.save(update_fields=["disbursement_account"])
    audit(user, "Payout account chosen", app, f"{account.bank_name} {account.masked}")

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
        problems = submission_problems(app)
        if problems:
            raise ValidationError(problems)
        app.disbursement_account = effective_account(app)  # explicit choice, else the customer's primary account
        app.submitted_at = timezone.now()
        app.interest_rate = app.loan_product.interest_rate  # freeze the rate this customer applied under
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
    payout = None
    if to == S.DISBURSED:
        if not note:
            raise ValidationError("A disbursement reference is required.")
        payout = effective_account(app)  # loans approved before accounts existed fall back to the customer's primary account
        if payout is None:
            raise ValidationError("The customer has no active bank account to pay the loan into.")
        app.disbursement_account = payout
    audit_note = _override_note(app, to, note) if to in (S.APPROVED, S.REJECTED) else note
    if payout: audit_note = f"{note} (paid into {payout.bank_name} {payout.masked})"
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
