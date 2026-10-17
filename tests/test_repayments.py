"""Disbursement, payments, late fees, early payoff, reminders and the repayment views."""
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import override_settings
from django.utils import timezone
from core import services
from core.models import *
from tests.test_hardening import Base

D = Decimal


class Loan(Base):
    """Helpers: an approved (not yet disbursed) loan, and a disbursed one."""
    def approved(self, **kw):
        app = self.app(**kw); self.to_manager_review(app); services.transition(app, Status.APPROVED, self.mg, "ok")
        return LoanApplication.objects.get(pk=app.pk)

    def disbursed(self, **kw):
        app = self.approved(**kw); services.transition(app, Status.DISBURSED, self.lo, "TXN-1")
        return LoanApplication.objects.get(pk=app.pk)

    def age(self, app, days):
        """Pretend the loan was disbursed `days` ago: shift disbursement and every due date back."""
        LoanApplication.objects.filter(pk=app.pk).update(disbursed_at=timezone.now() - timedelta(days=days))
        for i in app.schedule.all():
            RepaymentInstallment.objects.filter(pk=i.pk).update(due_date=i.due_date - timedelta(days=days))
        return LoanApplication.objects.get(pk=app.pk)

    def pay(self, app, amount, method="CASH", **kw):
        return services.record_payment(app, amount, method, kw.pop("user", self.lo), **kw)


class DisbursementTests(Loan):
    def test_flow_and_roles(self):
        app = self.approved()
        self.assertEqual(services.next_options(app, Role.LOAN_OFFICER), [Status.DISBURSED])
        self.assertEqual(services.next_options(app, Role.BRANCH_MANAGER), [])
        for u in (self.mg, self.co, self.c):
            with self.assertRaises(PermissionDenied): services.transition(app, Status.DISBURSED, u, "ref")
        with self.assertRaises(ValidationError): services.transition(app, Status.DISBURSED, self.lo, "")  # reference required
        services.transition(app, Status.DISBURSED, self.lo, "MM-REF-77")
        app.refresh_from_db(); self.assertEqual(app.status, Status.DISBURSED); self.assertIsNotNone(app.disbursed_at)
        self.assertEqual(app.schedule.count(), 12)
        self.assertEqual(app.schedule.first().due_date, services.add_months(timezone.localdate(), 1))
        self.assertTrue(Notification.objects.filter(recipient=self.c, notification_type=Status.DISBURSED).exists())

    def test_loan_officers_told_when_approved(self):
        self.approved()
        self.assertTrue(Notification.objects.filter(recipient=self.lo, notification_type=Status.APPROVED).exists())

    def test_completed_is_not_a_manual_transition(self):
        app = self.disbursed()
        self.assertEqual(services.next_options(app, Role.BRANCH_MANAGER), [])
        self.assertEqual(services.next_options(app, Role.SYSTEM_ADMIN), [])
        with self.assertRaises(ValidationError): services.transition(app, Status.COMPLETED, self.mg)

    def test_schedule_from_old_flow_is_rebuilt_at_disbursement(self):
        app = self.approved()
        stale = app.schedule.count(); self.assertEqual(stale, 0)
        services.make_schedule(app)  # what the old approve step used to do
        RepaymentInstallment.objects.filter(application=app).update(due_date=timezone.localdate() - timedelta(days=400))
        services.transition(app, Status.DISBURSED, self.lo, "ref")
        self.assertEqual(app.schedule.count(), 12)
        self.assertGreater(app.schedule.first().due_date, timezone.localdate())

    def test_schedule_with_payments_is_never_rewritten(self):
        app = self.disbursed(); self.pay(app, 1000)
        before = list(app.schedule.values_list("pk", flat=True)); services.make_schedule(app)
        self.assertEqual(list(app.schedule.values_list("pk", flat=True)), before)


class PaymentTests(Loan):
    def test_partial_payment_goes_to_oldest_installment_first(self):
        app = self.disbursed(); first = app.schedule.first()
        self.pay(app, first.amount - 100)
        first.refresh_from_db(); self.assertEqual(first.paid_amount, first.amount - 100); self.assertIsNone(first.paid_at)
        self.assertEqual(first.state(), "PARTIAL")
        self.pay(app, 100)
        first.refresh_from_db(); self.assertEqual(first.state(), "PAID"); self.assertIsNotNone(first.paid_at)
        self.assertEqual(app.schedule.get(number=2).paid_amount, 0)

    def test_payment_can_run_ahead_into_later_installments(self):
        app = self.disbursed(); inst = app.schedule.first().amount
        self.pay(app, inst * 2 + 50)
        s = list(app.schedule.all())
        self.assertEqual([i.state() for i in s[:3]], ["PAID", "PAID", "PARTIAL"]); self.assertEqual(s[2].paid_amount, 50)

    def test_payment_record_audit_and_notification(self):
        app = self.disbursed(); p = self.pay(app, 5000, "MOBILE_MONEY", reference="MM123")
        pay = Payment.objects.get(application=app)
        self.assertEqual((pay.amount, pay.method, pay.reference, pay.recorded_by), (D("5000.00"), "MOBILE_MONEY", "MM123", self.lo))
        self.assertEqual(AuditLog.objects.filter(application=app).latest("pk").action, "Payment recorded")
        self.assertTrue(Notification.objects.filter(recipient=self.c, notification_type="PAYMENT").exists())
        self.assertTrue(SmsLog.objects.filter(recipient=self.c, text__contains="5000").exists())

    def test_invalid_payments_rejected(self):
        app = self.disbursed(); owed = services.outstanding_total(app); today = timezone.localdate()
        for kw in ({"amount": 0}, {"amount": -5}, {"amount": "abc"}, {"amount": owed + 1},
                   {"amount": 100, "method": "BITCOIN"}, {"amount": 100, "paid_on": today + timedelta(days=1)},
                   {"amount": 100, "paid_on": today - timedelta(days=5)}):  # before disbursement
            kw.setdefault("method", "CASH")
            with self.assertRaises(ValidationError, msg=str(kw)): services.record_payment(app, kw.pop("amount"), kw.pop("method"), self.lo, **kw)
        self.assertEqual(Payment.objects.count(), 0)

    def test_only_loan_officers_record_payments_and_only_on_disbursed_loans(self):
        app = self.disbursed()
        for u in (self.c, self.co, self.mg, User.objects.get(username="admin")):
            with self.assertRaises(PermissionDenied): self.pay(app, 100, user=u)
        with self.assertRaises(ValidationError): self.pay(self.approved(), 100)

    def test_full_payment_completes_loan_automatically(self):
        app = self.disbursed(per=3); owed = services.outstanding_total(app)
        self.pay(app, owed)
        app.refresh_from_db(); self.assertEqual(app.status, Status.COMPLETED); self.assertIsNotNone(app.completed_at)
        self.assertEqual(services.outstanding_total(app), 0); self.assertTrue(all(i.state() == "PAID" for i in app.schedule.all()))
        self.assertTrue(Notification.objects.filter(recipient=self.c, notification_type=Status.COMPLETED).exists())
        with self.assertRaises(ValidationError): self.pay(app, 1)  # nothing left to pay

    def test_instalment_by_instalment_also_completes(self):
        app = self.disbursed(per=3)
        for i in app.schedule.all(): self.pay(app, i.amount)
        app.refresh_from_db(); self.assertEqual(app.status, Status.COMPLETED)

    def test_early_payoff_is_full_outstanding_without_interest_recalculation(self):
        app = self.disbursed(per=6); total = sum(i.amount for i in app.schedule.all())
        self.assertEqual(services.outstanding_total(app), total)
        self.pay(app, app.schedule.first().amount)
        self.assertEqual(services.outstanding_total(app), total - app.schedule.first().amount)


@override_settings(LATE_FEE_PERCENT="5", LATE_FEE_GRACE_DAYS=3)
class LateFeeTests(Loan):
    def test_no_fee_within_grace_then_fee_once(self):
        app = self.disbursed(); first = app.schedule.first()
        self.assertEqual(services.assess_late_fees(app, first.due_date + timedelta(days=3)), [])  # still inside the grace period
        charged = services.assess_late_fees(app, first.due_date + timedelta(days=4))
        self.assertEqual([i.number for i in charged], [1])
        first.refresh_from_db(); self.assertEqual(first.late_fee, (first.amount * D("0.05")).quantize(D("0.01")))
        before = first.late_fee; services.assess_late_fees(app, first.due_date + timedelta(days=9))
        first.refresh_from_db(); self.assertEqual(first.late_fee, before)  # one-off, never compounds
        self.assertEqual(AuditLog.objects.filter(application=app, action="Late fee assessed", description__contains="Installment 1").count(), 1)
        self.assertTrue(Notification.objects.filter(recipient=self.c, notification_type="LATE_FEE").exists())

    def test_fee_is_percentage_of_unpaid_part_only(self):
        app = self.disbursed(); first = app.schedule.first(); self.pay(app, first.amount - 1000)
        services.assess_late_fees(app, first.due_date + timedelta(days=10))
        first.refresh_from_db(); self.assertEqual(first.late_fee, D("50.00"))  # 5% of 1000 unpaid

    def test_paid_installment_never_gets_a_fee(self):
        app = self.disbursed(); first = app.schedule.first(); self.pay(app, first.amount)
        services.assess_late_fees(app, first.due_date + timedelta(days=30))
        first.refresh_from_db(); self.assertEqual(first.late_fee, 0)

    def test_payment_allocates_fee_before_installment(self):
        app = self.age(self.disbursed(), 40); first = app.schedule.first()
        services.assess_late_fees(app); first.refresh_from_db(); fee = first.late_fee; self.assertGreater(fee, 0)
        self.pay(app, fee + 10)
        first.refresh_from_db(); self.assertEqual((first.fee_paid, first.paid_amount), (fee, D("10.00")))
        self.assertEqual(first.state(), "OVERDUE")

    def test_backdated_on_time_payment_is_not_penalised(self):
        app = self.age(self.disbursed(), 40); first = app.schedule.first()
        self.pay(app, first.amount, paid_on=first.due_date)  # received on the due date, recorded late
        first.refresh_from_db(); self.assertEqual(first.late_fee, 0); self.assertEqual(first.state(), "PAID")

    def test_late_payment_incurs_fee_and_is_included_in_total_owed(self):
        app = self.age(self.disbursed(per=3), 40); first = app.schedule.first(); owed_before = services.outstanding_total(app)
        self.pay(app, first.amount)  # today, ~9 days after the first due date
        first.refresh_from_db(); self.assertGreater(first.late_fee, 0); self.assertEqual(first.state(), "OVERDUE")  # fee still unpaid
        self.assertEqual(services.outstanding_total(app), owed_before - first.amount + first.late_fee)

    @override_settings(LATE_FEE_PERCENT="10", LATE_FEE_GRACE_DAYS=0)
    def test_policy_is_configurable(self):
        app = self.disbursed(); first = app.schedule.first()
        services.assess_late_fees(app, first.due_date + timedelta(days=1))
        first.refresh_from_db(); self.assertEqual(first.late_fee, (first.amount * D("0.10")).quantize(D("0.01")))

    def test_loan_can_complete_after_paying_all_fees(self):
        app = self.age(self.disbursed(per=3), 100); services.assess_late_fees(app)
        owed = services.outstanding_total(app); self.assertGreater(owed, sum(i.amount for i in app.schedule.all()))
        self.pay(app, owed); app.refresh_from_db(); self.assertEqual(app.status, Status.COMPLETED)

    def test_fees_only_assessed_on_disbursed_loans(self):
        app = self.approved(); self.assertEqual(services.assess_late_fees(app, timezone.localdate() + timedelta(days=999)), [])


class ReminderAndCommandTests(Loan):
    def test_reminder_sent_once_inside_window(self):
        app = self.disbursed(); first = app.schedule.first()
        self.assertEqual(services.send_reminders(first.due_date - timedelta(days=10)), 0)
        self.assertEqual(services.send_reminders(first.due_date - timedelta(days=2)), 1)
        self.assertEqual(services.send_reminders(first.due_date - timedelta(days=1)), 0)  # already reminded
        self.assertTrue(SmsLog.objects.filter(recipient=self.c, text__contains="is due on").exists())

    def test_no_reminder_for_paid_installment(self):
        app = self.disbursed(); first = app.schedule.first(); self.pay(app, first.amount)
        self.assertEqual(services.send_reminders(first.due_date - timedelta(days=1)), 0)

    def test_management_command_runs_and_is_idempotent(self):
        app = self.age(self.disbursed(), 40)
        call_command("repayment_jobs"); fee = app.schedule.first().late_fee; self.assertGreater(fee, 0)
        call_command("repayment_jobs"); self.assertEqual(app.schedule.first().late_fee, fee)


class ViewTests(Loan):
    def test_loan_officer_records_payment_via_form(self):
        app = self.disbursed(); self.client.force_login(self.lo)
        r = self.client.post(f"/applications/{app.pk}/payments/", {"amount": "2500.00", "method": "CASH", "reference": "R1"}, follow=True)
        self.assertEqual(r.status_code, 200); self.assertEqual(Payment.objects.get().amount, D("2500.00"))
        self.assertContains(r, "Payment recorded")

    def test_form_errors_are_shown_not_crashed(self):
        app = self.disbursed(); self.client.force_login(self.lo)
        r = self.client.post(f"/applications/{app.pk}/payments/", {"amount": "99999999", "method": "CASH"}, follow=True)
        self.assertContains(r, "exceeds the outstanding balance"); self.assertEqual(Payment.objects.count(), 0)
        r = self.client.post(f"/applications/{app.pk}/payments/", {"amount": "", "method": "NOPE"}, follow=True)
        self.assertEqual(r.status_code, 200); self.assertEqual(Payment.objects.count(), 0)

    def test_others_cannot_post_payments(self):
        app = self.disbursed()
        for u in (self.c, self.co, self.mg):
            self.client.force_login(u)
            self.assertEqual(self.client.post(f"/applications/{app.pk}/payments/", {"amount": "100", "method": "CASH"}).status_code, 403)
        self.assertEqual(Payment.objects.count(), 0)

    def test_detail_pages_render_for_customer_and_staff(self):
        app = self.disbursed(); self.pay(app, 1234)
        self.client.force_login(self.c); r = self.client.get(f"/applications/{app.pk}/")
        self.assertContains(r, "settle in full"); self.assertContains(r, "1234.00"); self.assertNotContains(r, "Record payment")
        self.client.force_login(self.lo); self.assertContains(self.client.get(f"/applications/{app.pk}/"), "Record payment")

    def test_detail_page_assesses_late_fees_lazily(self):
        app = self.age(self.disbursed(), 40); self.client.force_login(self.c)
        self.assertContains(self.client.get(f"/applications/{app.pk}/"), "OVERDUE")
        self.assertGreater(app.schedule.first().late_fee, 0)

    def test_overdue_list_access_and_content(self):
        app = self.age(self.disbursed(), 40)
        self.client.force_login(self.lo); r = self.client.get("/overdue/")
        self.assertContains(r, app.application_number); self.assertContains(r, "customer1")
        self.client.force_login(self.mg); self.assertEqual(self.client.get("/overdue/").status_code, 200)
        for u in (self.c, self.co):
            self.client.force_login(u); self.assertEqual(self.client.get("/overdue/").status_code, 403)

    def test_overdue_list_empty_state(self):
        self.disbursed(); self.client.force_login(self.lo)
        self.assertContains(self.client.get("/overdue/"), "Nothing overdue")

    def test_disbursement_through_the_ui(self):
        app = self.approved(); self.client.force_login(self.lo)
        self.client.post(f"/applications/{app.pk}/transition/", {"to": "DISBURSED", "note": "BANK-REF-9"})
        app.refresh_from_db(); self.assertEqual(app.status, Status.DISBURSED)

    def test_dashboards_and_analytics(self):
        app = self.age(self.disbursed(), 40); self.pay(app, 1000)
        for u, code in ((self.lo, 200), (self.mg, 200), (self.c, 200)):
            self.client.force_login(u); self.assertEqual(self.client.get("/").status_code, code)
        self.client.force_login(self.lo); self.assertContains(self.client.get("/"), app.application_number)  # active loans visible to officer
        self.client.force_login(User.objects.get(username="admin"))
        r = self.client.get("/analytics/"); self.assertContains(r, "Loan book"); self.assertContains(r, "Collected: 1000")
