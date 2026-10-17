"""Regression tests for the workflow / validation / survey bugs found in the code review."""
from datetime import date
from decimal import Decimal
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from core import services
from core.models import *


class Base(TestCase):
    def setUp(self):
        call_command("seed_data", verbosity=0)
        g = User.objects.get
        self.c, self.lo, self.co, self.mg = g(username="customer1"), g(username="loanofficer"), g(username="creditofficer"), g(username="manager")
        self.p = LoanProduct.objects.get(name="Personal Loan")

    def app(self, **kw):
        return LoanApplication.objects.create(customer=self.c, loan_product=self.p, branch=Branch.objects.first(), purpose="x",
            amount_requested=kw.pop("amt", 1000000), repayment_period=kw.pop("per", 12), monthly_income=2000000, **kw)

    def doc(self, app, typ, status="VERIFIED", name="a.pdf"):
        return Document.objects.create(application=app, doc_type=typ, file="private/" + name, status=status)

    def to_document_review(self, app):
        services.transition(app, Status.SUBMITTED, self.c)
        services.transition(app, Status.DOCUMENT_REVIEW, self.lo)

    def verified_docs(self, app):
        for t in services.required_types(self.p):
            self.doc(app, t)

    ASSESS = {"risk_level": "LOW", "recommendation": "RECOMMEND_APPROVAL", "credit_history": "GOOD", "notes": "ok"}

    def to_manager_review(self, app, assess=None):
        self.to_document_review(app); self.verified_docs(app)
        services.transition(app, Status.CREDIT_ASSESSMENT, self.lo)
        services.transition(app, Status.MANAGER_REVIEW, self.co, assessment=assess or self.ASSESS)


class SurveyTests(Base):
    def test_partial_submission_does_not_crash_and_saves_answered(self):
        qs = list(SurveyQuestion.objects.all()); self.client.force_login(self.c)
        r = self.client.post("/survey/", {f"q{qs[0].pk}": "4"})
        self.assertLess(r.status_code, 500)
        self.assertEqual(list(SurveyAnswer.objects.values_list("score", flat=True)), [4])

    def test_out_of_range_scores_are_ignored(self):
        self.client.force_login(self.c)
        self.client.post("/survey/", {f"q{q.pk}": v for q, v in zip(SurveyQuestion.objects.all(), ["12", "0", "6", "23", "abc"])})
        self.assertEqual(SurveyAnswer.objects.count(), 0)

    def test_model_validates_range(self):
        a = SurveyAnswer(question=SurveyQuestion.objects.first(), respondent=self.c, score=12)
        with self.assertRaises(ValidationError): a.full_clean()


class ValidationTests(Base):
    def test_zero_month_period_rejected_and_page_does_not_crash(self):
        app = self.app(per=0)
        with self.assertRaises(ValidationError): services.transition(app, Status.SUBMITTED, self.c)
        self.client.force_login(self.c)
        self.assertEqual(self.client.get(f"/applications/{app.pk}/").status_code, 200)

    def test_installment_guards_zero_months(self):
        self.assertEqual(services.installment(1000, 10, 0), Decimal("0.00"))
        self.assertEqual(services.amortization(1000, 10, 0), [])

    def test_negative_money_fields_rejected_by_form(self):
        self.client.force_login(self.c)
        r = self.client.post(f"/apply/{self.p.pk}/", {"branch": Branch.objects.first().pk, "amount_requested": "1000000", "purpose": "x",
            "repayment_period": "12", "monthly_income": "1000", "monthly_expenses": "-5000000", "existing_debt": "0"})
        self.assertEqual(r.status_code, 200)  # form re-rendered with errors, nothing saved
        self.assertFalse(LoanApplication.objects.filter(monthly_expenses__lt=0).exists())

    def test_duplicate_nin_rejected(self):
        from core.views import ProfileForm
        other = User.objects.create_user("other", password="x"); Profile.objects.create(user=other, phone="0700000001")
        f = ProfileForm({"nin": self.c.profile.nin, "phone": "0700000001"}, instance=other.profile)
        self.assertFalse(f.is_valid()); self.assertIn("nin", f.errors)


class DocumentGateTests(Base):
    def test_superseded_rejected_doc_no_longer_blocks(self):
        app = self.app(); self.to_document_review(app)
        self.doc(app, "National ID", "REJECTED", "old.pdf")
        self.doc(app, "National ID", "VERIFIED", "new.pdf")
        self.doc(app, "Payslip")
        services.transition(app, Status.CREDIT_ASSESSMENT, self.lo)
        self.assertEqual(app.__class__.objects.get(pk=app.pk).status, Status.CREDIT_ASSESSMENT)

    def test_latest_upload_rejected_still_blocks(self):
        app = self.app(); self.to_document_review(app)
        self.doc(app, "National ID", "VERIFIED", "old.pdf"); self.doc(app, "Payslip")
        self.doc(app, "National ID", "REJECTED", "new.pdf")
        with self.assertRaises(ValidationError): services.transition(app, Status.CREDIT_ASSESSMENT, self.lo)

    def test_required_documents_are_enforced(self):
        app = self.app(); self.to_document_review(app)
        self.doc(app, "Passport Photo")
        with self.assertRaises(ValidationError) as cm: services.transition(app, Status.CREDIT_ASSESSMENT, self.lo)
        self.assertIn("National ID", " ".join(cm.exception.messages))

    def test_review_only_in_document_review_and_only_latest(self):
        app = self.app(); services.transition(app, Status.SUBMITTED, self.c)
        d = self.doc(app, "Payslip", "UPLOADED"); self.client.force_login(self.lo)
        self.client.post(f"/documents/{d.pk}/review/", {"status": "VERIFIED"})  # still SUBMITTED
        d.refresh_from_db(); self.assertEqual(d.status, "UPLOADED")
        services.transition(app, Status.DOCUMENT_REVIEW, self.lo)
        newer = self.doc(app, "Payslip", "UPLOADED", "n.pdf")
        self.client.post(f"/documents/{d.pk}/review/", {"status": "VERIFIED"})  # superseded
        d.refresh_from_db(); self.assertEqual(d.status, "UPLOADED")
        self.client.post(f"/documents/{newer.pk}/review/", {"status": "VERIFIED"})
        newer.refresh_from_db(); self.assertEqual(newer.status, "VERIFIED")

    def test_download_is_audited(self):
        app = self.app(); d = Document.objects.create(application=app, doc_type="Payslip", file=SimpleUploadedFile("x.pdf", b"%PDF-1.4 t"))
        self.client.force_login(self.lo); n = AuditLog.objects.count()
        self.assertEqual(self.client.get(f"/documents/{d.pk}/").status_code, 200)
        self.assertEqual(AuditLog.objects.count(), n + 1)
        self.assertEqual(AuditLog.objects.latest("pk").action, "Document downloaded")


class WorkflowTests(Base):
    def test_customer_cannot_self_advance_to_document_review(self):
        app = self.app(); services.transition(app, Status.SUBMITTED, self.c)
        with self.assertRaises(PermissionDenied): services.transition(app, Status.DOCUMENT_REVIEW, self.c)

    def test_customer_can_still_reply_to_information_request(self):
        app = self.app(); self.to_document_review(app)
        services.transition(app, Status.INFORMATION_REQUIRED, self.lo, "need payslip")
        services.transition(app, Status.DOCUMENT_REVIEW, self.c)

    def test_next_options_per_role(self):
        app = self.app(); services.transition(app, Status.SUBMITTED, self.c); app.refresh_from_db()
        self.assertEqual(services.next_options(app, Role.CUSTOMER), [Status.WITHDRAWN])
        self.assertEqual(services.next_options(app, Role.LOAN_OFFICER), [Status.DOCUMENT_REVIEW])
        self.assertEqual(services.next_options(app, Role.BRANCH_MANAGER), [])

    def test_assessment_is_atomic_and_validated(self):
        app = self.app(); self.client.force_login(self.co)  # DRAFT application, wrong state
        self.client.post(f"/applications/{app.pk}/transition/", {"to": "MANAGER_REVIEW", "risk": "BANANA", "recommendation": "X"})
        self.assertEqual(app.assessments.count(), 0)
        app2 = self.app(); self.to_document_review(app2); self.verified_docs(app2)
        services.transition(app2, Status.CREDIT_ASSESSMENT, self.lo)
        with self.assertRaises(ValidationError):
            services.transition(app2, Status.MANAGER_REVIEW, self.co, assessment={**self.ASSESS, "risk_level": "BANANA"})
        with self.assertRaises(ValidationError): services.transition(app2, Status.MANAGER_REVIEW, self.co)  # none supplied
        self.assertEqual(app2.assessments.count(), 0)
        services.transition(app2, Status.MANAGER_REVIEW, self.co, assessment=self.ASSESS)
        self.assertEqual(app2.assessments.count(), 1)

    def test_override_of_credit_recommendation_is_audited(self):
        app = self.app(); self.to_manager_review(app, {**self.ASSESS, "risk_level": "HIGH", "recommendation": "RECOMMEND_REJECTION"})
        services.transition(app, Status.APPROVED, self.mg, "exception granted")
        self.assertIn("OVERRIDE", AuditLog.objects.filter(application=app).latest("pk").description)

    def test_normal_approval_is_not_flagged(self):
        app = self.app(); self.to_manager_review(app); services.transition(app, Status.APPROVED, self.mg, "ok")
        self.assertNotIn("OVERRIDE", AuditLog.objects.filter(application=app).latest("pk").description)

    def test_staff_are_notified_when_work_arrives(self):
        app = self.app(); services.transition(app, Status.SUBMITTED, self.c)
        self.assertTrue(Notification.objects.filter(recipient=self.lo).exists())
        self.assertFalse(Notification.objects.filter(recipient=self.co).exists())
        services.transition(app, Status.DOCUMENT_REVIEW, self.lo); self.verified_docs(app)
        services.transition(app, Status.CREDIT_ASSESSMENT, self.lo)
        self.assertTrue(Notification.objects.filter(recipient=self.co).exists())
        services.transition(app, Status.MANAGER_REVIEW, self.co, assessment=self.ASSESS)
        self.assertTrue(Notification.objects.filter(recipient=self.mg).exists())


class MoneyTests(Base):
    def test_rate_frozen_at_submission(self):
        app = self.app(); services.transition(app, Status.SUBMITTED, self.c)
        app.refresh_from_db(); frozen = app.interest_rate; self.assertEqual(frozen, self.p.interest_rate)
        before = services.affordability(app)["installment"]
        self.p.interest_rate = Decimal("30.00"); self.p.save(); app.refresh_from_db()
        self.assertEqual(app.interest_rate, frozen); self.assertEqual(services.affordability(app)["installment"], before)

    def test_approval_creates_exact_schedule_with_calendar_dates(self):
        app = self.app(amt=1000000, per=7); self.to_manager_review(app); services.transition(app, Status.APPROVED, self.mg, "ok")
        rows = list(app.schedule.all()); self.assertEqual(len(rows), 7)
        self.assertEqual(sum(r.principal for r in rows), Decimal("1000000.00"))   # principal repaid exactly
        self.assertEqual(rows[-1].balance, Decimal("0.00"))
        self.assertTrue(all(r.amount == r.principal + r.interest for r in rows))
        today = date.today(); self.assertEqual(rows[0].due_date.day, min(today.day, rows[0].due_date.day))
        self.assertTrue(all(rows[i].due_date < rows[i + 1].due_date for i in range(6)))

    def test_add_months_clamps_month_end(self):
        self.assertEqual(services.add_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        self.assertEqual(services.add_months(date(2026, 11, 30), 3), date(2027, 2, 28))
        self.assertEqual(services.add_months(date(2026, 12, 15), 1), date(2027, 1, 15))

    def test_zero_interest_schedule(self):
        rows = services.amortization(1200, 0, 12)
        self.assertEqual(sum(r[2] for r in rows), Decimal("1200.00")); self.assertEqual(rows[-1][3], Decimal("0.00"))
