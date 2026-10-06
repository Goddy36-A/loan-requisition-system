import statistics
from django import forms
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import UserCreationForm
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Avg, F
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
    return render(request, "core/detail.html", {"app": app, "role": role, "trail": trail, "aff": services.affordability(app), "docs": app.documents.all(),
        "sched": app.schedule.all(), "doc_types": DOC_TYPES, "can_review": role == Role.LOAN_OFFICER,
        "can_upload": role == Role.CUSTOMER and app.status in (Status.DRAFT, Status.INFORMATION_REQUIRED),
        "next": sorted(services.ALLOWED.get(app.status, set()) & {t for t, rs in services.ROLES_FOR.items() if role in rs}),
        "assessments": app.assessments.all() if role != Role.CUSTOMER else []})

@login_required
@require_POST
def do_transition(request, pk):
    app, role = _visible(request.user, pk)
    to = request.POST.get("to", "")
    try:
        if to == Status.MANAGER_REVIEW and role == Role.CREDIT_OFFICER:
            Assessment.objects.create(application=app, assessor=request.user, risk_level=request.POST.get("risk", "MEDIUM"),
                recommendation=request.POST.get("recommendation", "REQUIRE_MORE_INFORMATION"), credit_history=request.POST.get("credit_history", "NONE"), notes=request.POST.get("note", ""))
        services.transition(app, to, request.user, request.POST.get("note", "").strip())
    except ValidationError as e: messages.error(request, "; ".join(e.messages))
    return redirect("detail", pk)

@login_required
def survey(request):
    qs = SurveyQuestion.objects.order_by("category", "id")
    if request.method == "POST":
        for q in qs:
            v = request.POST.get(f"q{q.pk}")
            if v in "12345" and v:
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
    return render(request, "core/analytics.html", ctx)

import csv, os, uuid
from django.http import FileResponse, HttpResponse
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
    return FileResponse(doc.file.open("rb"), as_attachment=True, filename=doc.doc_type.replace(" ", "_") + os.path.splitext(doc.file.name)[1])

@login_required
@require_POST
def review_doc(request, doc_id):
    doc = get_object_or_404(Document, pk=doc_id)
    if services.role_of(request.user) != Role.LOAN_OFFICER: raise PermissionDenied
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
