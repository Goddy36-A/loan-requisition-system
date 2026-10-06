from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from core.models import *
from core import services

class WorkflowTests(TestCase):
    def setUp(self):
        call_command("seed_data"); call_command("seed_data")  # idempotent
        self.c = User.objects.get(username="customer1"); self.lo = User.objects.get(username="loanofficer")
        p = LoanProduct.objects.first()
        self.app = LoanApplication.objects.create(customer=self.c, loan_product=p, branch=Branch.objects.first(),
            amount_requested=p.min_amount, purpose="x", repayment_period=6)
    def test_unique_number_and_seed(self):
        self.assertTrue(self.app.application_number.startswith("LN-")); self.assertEqual(User.objects.count(), 6)
    def test_invalid_transition(self):
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError): services.transition(self.app, Status.APPROVED, self.c)
    def test_customer_cannot_see_other(self):
        other = User.objects.create_user("c2", password="x"); Profile.objects.create(user=other)
        self.client.force_login(other)
        self.assertEqual(self.client.get(f"/applications/{self.app.pk}/").status_code, 403)
    def test_flow_start(self):
        services.transition(self.app, Status.SUBMITTED, self.c)
        services.transition(self.app, Status.DOCUMENT_REVIEW, self.lo)
        self.assertEqual(Notification.objects.filter(recipient=self.c).count(), 1)
