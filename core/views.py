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
                  "employer_name", "monthly_income", "monthly_expenses", "existing_debt"]

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
    return render(request, "core/detail.html", {"app": app, "role": role, "trail": trail,
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
                recommendation=request.POST.get("recommendation", "REQUIRE_MORE_INFORMATION"), notes=request.POST.get("note", ""))
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
