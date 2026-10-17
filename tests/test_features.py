from decimal import Decimal
from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase
from core.models import *
from core import services

class FeatureTests(TestCase):
    def setUp(self):
        call_command("seed_data")
        self.c = User.objects.get(username="customer1"); self.lo = User.objects.get(username="loanofficer")
        p = LoanProduct.objects.first()
        self.app = LoanApplication.objects.create(customer=self.c, loan_product=p, branch=Branch.objects.first(),
            amount_requested=1000000, purpose="x", repayment_period=12, monthly_income=2000000)
    def test_installment(self):
        self.assertEqual(services.installment(1200, 0, 12), Decimal("100.00"))
        self.assertTrue(services.affordability(self.app)["affordable"])
    def test_upload_validation_and_protection(self):
        self.client.force_login(self.c)
        bad = SimpleUploadedFile("x.pdf", b"not a pdf"); self.client.post(f"/applications/{self.app.pk}/upload/", {"doc_type": "Payslip", "file": bad})
        self.assertEqual(Document.objects.count(), 0)
        good = SimpleUploadedFile("x.pdf", b"%PDF-1.4 test"); self.client.post(f"/applications/{self.app.pk}/upload/", {"doc_type": "Payslip", "file": good})
        d = Document.objects.get(); self.assertEqual(self.client.get(f"/documents/{d.pk}/").status_code, 200)
        o = User.objects.create_user("c2", password="x"); Profile.objects.create(user=o); self.client.force_login(o)
        self.assertEqual(self.client.get(f"/documents/{d.pk}/").status_code, 403)
    def test_submission_sends_sms(self):
        Witness.objects.create(application=self.app, full_name="W", phone="+256700555001", relationship="Friend")
        services.transition(self.app, Status.SUBMITTED, self.c)
        self.assertEqual(SmsLog.objects.filter(recipient=self.c).count(), 1)
    def test_csv_requires_research_role(self):
        self.client.force_login(self.c); self.assertEqual(self.client.get("/analytics/survey.csv").status_code, 403)
        self.client.force_login(User.objects.get(username="researcher")); self.assertEqual(self.client.get("/analytics/survey.csv").status_code, 200)
