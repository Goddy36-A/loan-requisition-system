import statistics
from django import forms
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Avg, DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from . import services
from .models import *

def register(request):
    form = UserCreationForm(request.POST or None)
    if form.is_valid():
        u = form.save(); Profile.objects.create(user=u, role=Role.CUSTOMER)
        login(request, u); return redirect("dashboard")
    return render(request, "registration/register.html", {"form": form})

class AppForm(forms.ModelForm):
    class Meta:
        model = LoanApplication
        fields = ["branch", "amount_requested", "purpose", "repayment_period", "employment_status",
                  "employer_name", "monthly_income", "monthly_expenses", "existing_debt", "guarantor_name", "guarantor_phone"]

@login_required
def dashboard(request):
    role = services.role_of(request.user)
    qs = LoanApplication.objects.select_related("customer", "loan_product", "branch").order_by("-created_at")
    if role == Role.CUSTOMER or role is None:
        qs = qs.filter(customer=request.user)
    elif role == Role.RESEARCH_ANALYST:
        qs = qs.none()
    elif role in services.QUEUES:
        qs = qs.filter(status__in=services.QUEUES[role])
    counts = {s: LoanApplication.objects.filter(customer=request.user, status=s).count() for s in Status.values} \
        if role == Role.CUSTOMER else {}
    notes = request.user.notifications.filter(is_read=False)[:5]
    return render(request, "core/dashboard.html", {"apps": qs[:50], "role": role, "counts": counts, "notes": notes})

@login_required
def products(request):
    return render(request, "core/products.html", {"products": LoanProduct.objects.filter(is_active=True)})

@login_required
def apply(request, product_id):
    if services.role_of(request.user) != Role.CUSTOMER: raise PermissionDenied
    product = get_object_or_404(LoanProduct, pk=product_id, is_active=True)
    form = AppForm(request.POST or None)
    if form.is_valid():
        app = form.save(commit=False); app.customer, app.loan_product = request.user, product; app.save()
        services.audit(request.user, "Application created", app)
        if "submit" in request.POST:
            try: services.transition(app, Status.SUBMITTED, request.user)
            except ValidationError as e: messages.error(request, "; ".join(e.messages)); return redirect("detail", app.pk)
        return redirect("detail", app.pk)
    return render(request, "core/apply.html", {"form": form, "product": product})

def _visible(user, pk):
    app = get_object_or_404(LoanApplication.objects.select_related("customer", "loan_product", "branch"), pk=pk)
    role = services.role_of(user)
    if role == Role.CUSTOMER and app.customer_id != user.id: raise PermissionDenied
    if role in (None, Role.RESEARCH_ANALYST): raise PermissionDenied
    return app, role

@login_required
def detail(request, pk):
    app, role = _visible(request.user, pk)
    trail = app.auditlog_set.select_related("user") if role != Role.CUSTOMER else []
    latest_ids = {d.pk for d in services.latest_documents(app).values()}
    show_checklist = app.status in (Status.DRAFT, Status.INFORMATION_REQUIRED, Status.DOCUMENT_REVIEW)
    services.assess_late_fees(app)  # no-op unless DISBURSED; keeps fees current without needing a scheduler
    today = timezone.localdate()
    sched = list(app.schedule.all())
    for i in sched: i.state_label, i.late_days = i.state(today), i.days_late(today)
    pct, grace = services.late_fee_policy()
    return render(request, "core/detail.html", {"app": app, "role": role, "trail": trail, "aff": services.affordability(app), "docs": app.documents.all(),
        "sched": sched, "doc_types": DOC_TYPES, "latest_ids": latest_ids,
        "summary": services.repayment_summary(app, today) if sched else None, "payments": app.payments.all(),
        "can_record_payment": role == Role.LOAN_OFFICER and app.status == Status.DISBURSED,
        "payment_methods": Payment.METHODS, "fee_percent": pct, "grace_days": grace,
        "can_review": role == Role.LOAN_OFFICER and app.status == Status.DOCUMENT_REVIEW,
        "can_upload": role == Role.CUSTOMER and app.status in (Status.DRAFT, Status.INFORMATION_REQUIRED),
        "doc_problems": services.document_problems(app) if show_checklist else [],
        "next": services.next_options(app, role),
        "assessments": app.assessments.all() if role != Role.CUSTOMER else []})

@login_required
@require_POST
def do_transition(request, pk):
    app, role = _visible(request.user, pk)
    to = request.POST.get("to", "")
    assessment = None
    if to == Status.MANAGER_REVIEW and role == Role.CREDIT_OFFICER:
        assessment = {"risk_level": request.POST.get("risk"), "recommendation": request.POST.get("recommendation"),
                      "credit_history": request.POST.get("credit_history", "NONE"), "notes": request.POST.get("note", "")}
    try:
        services.transition(app, to, request.user, request.POST.get("note", "").strip(), assessment=assessment)
    except ValidationError as e: messages.error(request, "; ".join(e.messages))
    return redirect("detail", pk)

class PaymentForm(forms.Form):
    amount = forms.DecimalField(min_value=0.01, max_digits=14, decimal_places=2)
    method = forms.ChoiceField(choices=Payment.METHODS)
    reference = forms.CharField(max_length=60, required=False)
    paid_on = forms.DateField(required=False)

@login_required
@require_POST
def record_payment(request, pk):
    app, role = _visible(request.user, pk)
    if role != Role.LOAN_OFFICER: raise PermissionDenied
    form = PaymentForm(request.POST)
    if not form.is_valid():
        messages.error(request, "; ".join(f"{k}: {' '.join(v)}" for k, v in form.errors.items()))
        return redirect("detail", pk)
    d = form.cleaned_data
    try:
        services.record_payment(app, d["amount"], d["method"], request.user, d["reference"], d["paid_on"])
        messages.success(request, "Payment recorded.")
    except ValidationError as e: messages.error(request, "; ".join(e.messages))
    return redirect("detail", pk)

@login_required
def overdue(request):
    if services.role_of(request.user) not in (Role.LOAN_OFFICER, Role.BRANCH_MANAGER, Role.SYSTEM_ADMIN): raise PermissionDenied
    services.assess_all_late_fees()
    today = timezone.localdate()
    rows = list(RepaymentInstallment.objects.filter(application__status=Status.DISBURSED, due_date__lt=today)
        .filter(Q(paid_amount__lt=F("amount")) | Q(fee_paid__lt=F("late_fee")))
        .select_related("application__customer", "application__loan_product").order_by("due_date", "application_id"))
    for r in rows: r.late_days = r.days_late(today)
    return render(request, "core/overdue.html", {"rows": rows, "total": sum((r.outstanding for r in rows), 0),
        "fees": sum((r.fee_due for r in rows), 0), "loans": len({r.application_id for r in rows})})

@login_required
def survey(request):
    qs = SurveyQuestion.objects.order_by("category", "id")
    if request.method == "POST":
        for q in qs:
            v = request.POST.get(f"q{q.pk}")
            if v in ("1", "2", "3", "4", "5"):
                SurveyAnswer.objects.update_or_create(question=q, respondent=request.user, defaults={"score": int(v)})
        messages.success(request, "Thank you."); return redirect("dashboard")
    return render(request, "core/survey.html", {"questions": qs})

@login_required
def analytics(request):
    if services.role_of(request.user) not in (Role.RESEARCH_ANALYST, Role.SYSTEM_ADMIN): raise PermissionDenied
    apps = LoanApplication.objects
    done = list(apps.filter(decided_by__isnull=False, submitted_at__isnull=False).values_list("submitted_at", "approved_at", "rejected_at"))
    hrs = [((a or r) - s).total_seconds() / 3600 for s, a, r in done if (a or r)]
    cats = SurveyAnswer.objects.values("question__category").annotate(mean=Avg("score"))
    ctx = {"total": apps.count(), "by_status": {s: apps.filter(status=s).count() for s in Status.values},
           "avg_hours": round(statistics.mean(hrs), 2) if hrs else None, "cats": cats,
           "n_responses": SurveyAnswer.objects.values("respondent").distinct().count()}
    money = lambda e: ExpressionWrapper(e, output_field=DecimalField(max_digits=16, decimal_places=2))
    live = RepaymentInstallment.objects.filter(application__status=Status.DISBURSED)
    book = live.aggregate(owed=Sum(money(F("amount") - F("paid_amount") + F("late_fee") - F("fee_paid"))), fees=Sum("late_fee"))
    late = live.filter(due_date__lt=timezone.localdate()).aggregate(owed=Sum(money(F("amount") - F("paid_amount") + F("late_fee") - F("fee_paid"))))
    ctx["book"] = {"disbursed": apps.filter(disbursed_at__isnull=False).aggregate(t=Sum("amount_requested"))["t"] or 0,
        "outstanding": book["owed"] or 0, "late_fees": book["fees"] or 0, "overdue": late["owed"] or 0,
        "collected": Payment.objects.aggregate(t=Sum("amount"))["t"] or 0}
    return render(request, "core/analytics.html", ctx)

import csv, os, uuid
from django.http import FileResponse, Http404, HttpResponse
DOC_TYPES = ["National ID", "Payslip", "Employment Letter", "Bank Statement", "Passport Photo", "Supporting Document"]
MAGIC = {".pdf": b"%PDF", ".png": b"\x89PNG", ".jpg": b"\xff\xd8", ".jpeg": b"\xff\xd8"}

@login_required
@require_POST
def upload(request, pk):
    app, role = _visible(request.user, pk)
    if role != Role.CUSTOMER or app.status not in (Status.DRAFT, Status.INFORMATION_REQUIRED): raise PermissionDenied
    f, dtype = request.FILES.get("file"), request.POST.get("doc_type")
    ext = os.path.splitext(f.name)[1].lower() if f else ""
    if not f or dtype not in DOC_TYPES: messages.error(request, "Choose a document type and file.")
    elif ext not in MAGIC or f.size > 5 * 1024 * 1024 or not f.read(4).startswith(MAGIC[ext]):
        messages.error(request, "Only genuine PDF/JPG/PNG files up to 5 MB are accepted.")
    else:
        f.seek(0); f.name = uuid.uuid4().hex + ext  # never trust the client filename
        Document.objects.create(application=app, doc_type=dtype, file=f)
        services.audit(request.user, "Document uploaded", app, dtype)
    return redirect("detail", pk)

@login_required
def download(request, doc_id):
    doc = get_object_or_404(Document, pk=doc_id)
    _visible(request.user, doc.application_id)  # owner or staff only
    try: fh = doc.file.open("rb")
    except FileNotFoundError: raise Http404("Document file is missing.")
    services.audit(request.user, "Document downloaded", doc.application, doc.doc_type)
    return FileResponse(fh, as_attachment=True, filename=doc.doc_type.replace(" ", "_") + os.path.splitext(doc.file.name)[1])

@login_required
@require_POST
def review_doc(request, doc_id):
    doc = get_object_or_404(Document, pk=doc_id)
    if services.role_of(request.user) != Role.LOAN_OFFICER: raise PermissionDenied
    if doc.application.status != Status.DOCUMENT_REVIEW:
        messages.error(request, "Documents can only be reviewed while the application is in document review.")
        return redirect("detail", doc.application_id)
    if doc.pk not in {d.pk for d in services.latest_documents(doc.application).values()}:
        messages.error(request, "That upload has been superseded by a newer one.")
        return redirect("detail", doc.application_id)
    st = request.POST.get("status")
    if st in ("VERIFIED", "REJECTED"):
        doc.status, doc.reviewer, doc.comments = st, request.user, request.POST.get("comments", "")
        doc.save(); services.audit(request.user, f"Document {st.lower()}", doc.application, doc.doc_type)
        if st == "REJECTED":
            Notification.objects.create(recipient=doc.application.customer, title="Document rejected",
                notification_type="DOC_REJECTED", message=f"{doc.application.application_number}: {doc.doc_type} - {doc.comments}")
    return redirect("detail", doc.application_id)

class ProfileForm(forms.ModelForm):
    class Meta: model = Profile; fields = ["phone", "nin"]
    def clean_nin(self):
        n = self.cleaned_data["nin"].strip().upper()
        if n and (len(n) != 14 or not n.isalnum()): raise forms.ValidationError("NIN must be 14 letters/digits.")
        if n and Profile.objects.filter(nin=n).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This NIN is already registered to another account.")
        return n

@login_required
def profile(request):
    p, _ = Profile.objects.get_or_create(user=request.user)
    form = ProfileForm(request.POST or None, instance=p)
    if form.is_valid(): form.save(); messages.success(request, "Profile saved."); return redirect("profile")
    return render(request, "core/profile.html", {"form": form})

@login_required
def notifications(request):
    if request.method == "POST": request.user.notifications.update(is_read=True); return redirect("notifications")
    return render(request, "core/notifications.html", {"notes": request.user.notifications.order_by("-created_at")[:50],
        "sms": request.user.sms_log.order_by("-created_at")[:50]})

@login_required
def survey_csv(request):
    if services.role_of(request.user) not in (Role.RESEARCH_ANALYST, Role.SYSTEM_ADMIN): raise PermissionDenied
    resp = HttpResponse(content_type="text/csv"); resp["Content-Disposition"] = "attachment; filename=survey_responses.csv"
    w = csv.writer(resp); w.writerow(["respondent_code", "category", "question", "score", "date"])
    for a in SurveyAnswer.objects.select_related("question").order_by("respondent_id", "question_id"):
        w.writerow([f"R{a.respondent_id}", a.question.category, a.question.text, a.score, a.created_at.date()])
    return resp
