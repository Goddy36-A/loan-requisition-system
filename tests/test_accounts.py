"""Account management: bank accounts, next of kin, witnesses, submission rules, account pages, wording, UI helpers."""
from pathlib import Path
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import override_settings
from core import services, validators
from core.models import *
from core.templatetags.ui import money, status_class, status_label
from core.views import _stepper
from tests.test_hardening import Base

ACCT = {"bank_name": "Stanbic Bank Uganda", "account_name": "Test Person", "account_number": "9030001234567", "branch_name": ""}
KIN = {"full_name": "Mary Kin", "relationship": "Parent", "phone": "0700 111 222", "address": ""}
WIT = {"full_name": "Wilson Witness", "phone": "0771 000 111", "nin": "", "relationship": "Friend", "address": ""}


class Acct(Base):
    def new_customer(self, name="newbie", nin="NB12345678ABCD", phone="+256700999001"):
        u = User.objects.create_user(name, password="pw-12345-xyz")
        Profile.objects.create(user=u, role=Role.CUSTOMER, nin=nin, phone=phone)
        return u

    def ready_customer(self, name="ready"):
        u = self.new_customer(name, nin=f"{name.upper():0<14}"[:14], phone="+256700999002")
        services.add_bank_account(u, ACCT); services.add_next_of_kin(u, KIN)
        return u

    def draft(self, user, **kw):
        return LoanApplication.objects.create(customer=user, loan_product=self.p, branch=Branch.objects.first(), purpose="x",
            amount_requested=1000000, repayment_period=12, monthly_income=2000000, **kw)


class ValidatorTests(Acct):
    def test_phone_normalisation(self):
        for raw, want in (("0700 000 001", "+256700000001"), ("+256 700-000-001", "+256700000001"), ("256700000001", "+256700000001"),
                          ("00256700000001", "+256700000001"), ("+254712345678", "+254712345678")):
            self.assertEqual(validators.clean_phone(raw), want, raw)

    def test_bad_phones_rejected(self):
        for raw in ("", "abc", "123", "+25670000", "07000000011111111111"):
            with self.assertRaises(ValidationError, msg=raw): validators.clean_phone(raw)

    def test_nin_and_account_number(self):
        self.assertEqual(validators.clean_nin(" cm12345678abcd "), "CM12345678ABCD"); self.assertEqual(validators.clean_nin(""), "")
        with self.assertRaises(ValidationError): validators.clean_nin("short")
        self.assertEqual(validators.clean_account_number("3100 0123-4567"), "310001234567")
        for bad in ("12345", "abc12345", "1" * 21, ""):
            with self.assertRaises(ValidationError, msg=bad): validators.clean_account_number(bad)


class BankAccountTests(Acct):
    def test_first_account_is_primary_then_switch(self):
        u = self.new_customer(); a = services.add_bank_account(u, ACCT)
        b = services.add_bank_account(u, {**ACCT, "bank_name": "DFCU Bank", "account_number": "5550001111"})
        self.assertTrue(a.is_primary); self.assertFalse(b.is_primary)
        services.set_primary_account(u, b); a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.is_primary, b.is_primary), (False, True)); self.assertEqual(services.primary_account(u), b)

    def test_removing_primary_promotes_another_and_archives(self):
        u = self.new_customer(); a = services.add_bank_account(u, ACCT)
        b = services.add_bank_account(u, {**ACCT, "account_number": "5550001111"})
        services.remove_bank_account(u, a); a.refresh_from_db(); b.refresh_from_db()
        self.assertFalse(a.is_active); self.assertTrue(b.is_primary); self.assertTrue(BankAccount.objects.filter(pk=a.pk).exists())  # archived, not deleted
        services.remove_bank_account(u, b); self.assertFalse(services.active_accounts(u).exists())

    def test_cannot_remove_account_used_by_a_loan_in_progress(self):
        u = self.c; acct = services.primary_account(u); app = self.app(disbursement_account=acct)
        services.transition(app, Status.SUBMITTED, u)
        with self.assertRaises(ValidationError): services.remove_bank_account(u, acct)

    def test_can_remove_account_after_loan_finished_and_draft_is_unlinked(self):
        u = self.c; acct = services.primary_account(u)
        draft = self.app(disbursement_account=acct)
        done = self.app(disbursement_account=acct); LoanApplication.objects.filter(pk=done.pk).update(status=Status.COMPLETED)
        services.remove_bank_account(u, acct)
        draft.refresh_from_db(); self.assertIsNone(draft.disbursement_account)  # draft re-picks an account at submission

    def test_limits_duplicates_ownership_and_role(self):
        u = self.new_customer()
        for i in range(services.MAX_BANK_ACCOUNTS): services.add_bank_account(u, {**ACCT, "account_number": f"70000000{i}1"})
        with self.assertRaises(ValidationError): services.add_bank_account(u, {**ACCT, "account_number": "999999991"})
        v = self.new_customer("v2", nin="V2123456789012", phone="+256700999003"); a = services.add_bank_account(v, ACCT)
        with self.assertRaises(ValidationError): services.add_bank_account(v, ACCT)  # duplicate
        with self.assertRaises(PermissionDenied): services.set_primary_account(u, a)
        with self.assertRaises(PermissionDenied): services.remove_bank_account(u, a)
        with self.assertRaises(PermissionDenied): services.add_bank_account(self.lo, ACCT)

    def test_masked_number_hides_all_but_last_four(self):
        a = services.add_bank_account(self.new_customer(), ACCT)
        self.assertTrue(a.masked.endswith("4567")); self.assertNotIn("90300012", a.masked)

    def test_changes_are_audited_without_the_full_number(self):
        u = self.new_customer(); a = services.add_bank_account(u, ACCT)
        row = AuditLog.objects.get(action="Bank account added")
        self.assertIn("4567", row.description); self.assertNotIn(a.account_number, row.description)


class NextOfKinTests(Acct):
    def test_add_normalises_phone_and_limits(self):
        u = self.new_customer(); k = services.add_next_of_kin(u, KIN)
        self.assertEqual(k.phone, "+256700111222")
        for i in range(services.MAX_NEXT_OF_KIN - 1): services.add_next_of_kin(u, {**KIN, "full_name": f"K{i}"})
        with self.assertRaises(ValidationError): services.add_next_of_kin(u, KIN)

    def test_last_next_of_kin_protected_while_loan_in_progress(self):
        u = self.c; app = self.app(); services.transition(app, Status.SUBMITTED, u)
        only = u.next_of_kin.get()
        with self.assertRaises(ValidationError): services.remove_next_of_kin(u, only)
        services.add_next_of_kin(u, KIN); services.remove_next_of_kin(u, only)  # fine once another exists
        self.assertEqual(u.next_of_kin.count(), 1)

    def test_can_remove_last_kin_with_no_loan_in_progress(self):
        u = self.c; services.remove_next_of_kin(u, u.next_of_kin.get()); self.assertEqual(u.next_of_kin.count(), 0)

    def test_staff_and_other_customers_cannot(self):
        with self.assertRaises(PermissionDenied): services.add_next_of_kin(self.lo, KIN)
        other = self.new_customer()
        with self.assertRaises(PermissionDenied): services.remove_next_of_kin(other, self.c.next_of_kin.get())


class WitnessTests(Acct):
    def test_add_and_remove(self):
        app = self.app(witness=False); w = services.add_witness(app, self.c, WIT)
        self.assertEqual(w.phone, "+256771000111"); self.assertEqual(app.witnesses.count(), 1)
        services.remove_witness(self.c, w); self.assertEqual(app.witnesses.count(), 0)

    def test_applicant_cannot_witness_their_own_loan(self):
        app = self.app(witness=False)
        with self.assertRaises(ValidationError): services.add_witness(app, self.c, {**WIT, "phone": self.c.profile.phone})
        with self.assertRaises(ValidationError): services.add_witness(app, self.c, {**WIT, "nin": self.c.profile.nin.lower()})
        with self.assertRaises(ValidationError): services.add_witness(app, self.c, {**WIT, "phone": "0700000001"})  # same number, local format

    def test_guarantor_duplicates_and_max(self):
        app = self.app(witness=False, guarantor_name="G", guarantor_phone="0771 000 111")
        with self.assertRaises(ValidationError): services.add_witness(app, self.c, WIT)  # that is the guarantor's number
        services.add_witness(app, self.c, {**WIT, "phone": "0772000001"})
        with self.assertRaises(ValidationError): services.add_witness(app, self.c, {**WIT, "phone": "+256772000001"})  # duplicate
        for i in range(services.witness_limits()[1] - 1): services.add_witness(app, self.c, {**WIT, "phone": f"07730000{i:02d}"})
        with self.assertRaises(ValidationError): services.add_witness(app, self.c, {**WIT, "phone": "0774000000"})

    def test_only_owner_and_only_while_editable(self):
        app = self.app(witness=False)
        for u in (self.lo, self.new_customer()):
            with self.assertRaises(PermissionDenied): services.add_witness(app, u, WIT)
        w = services.add_witness(app, self.c, WIT); services.transition(app, Status.SUBMITTED, self.c)
        with self.assertRaises(ValidationError): services.add_witness(app, self.c, {**WIT, "phone": "0772000002"})
        with self.assertRaises(ValidationError): services.remove_witness(self.c, w)
        services.transition(app, Status.DOCUMENT_REVIEW, self.lo); services.transition(app, Status.INFORMATION_REQUIRED, self.lo, "witness id unclear")
        services.add_witness(app, self.c, {**WIT, "phone": "0772000003"})  # editable again when more information is requested


class SubmissionRuleTests(Acct):
    def test_each_requirement_is_enforced_and_all_listed_together(self):
        u = self.new_customer(); app = self.draft(u)
        with self.assertRaises(ValidationError) as cm: services.transition(app, Status.SUBMITTED, u)
        text = " ".join(cm.exception.messages)
        self.assertIn("bank account", text); self.assertIn("next of kin", text); self.assertIn("witness", text)

    def test_bank_account_is_required(self):
        u = self.new_customer(); services.add_next_of_kin(u, KIN); app = self.draft(u); services.add_witness(app, u, WIT)
        with self.assertRaises(ValidationError) as cm: services.transition(app, Status.SUBMITTED, u)
        self.assertIn("bank account", " ".join(cm.exception.messages))

    def test_next_of_kin_is_required(self):
        u = self.new_customer(); services.add_bank_account(u, ACCT); app = self.draft(u); services.add_witness(app, u, WIT)
        with self.assertRaises(ValidationError) as cm: services.transition(app, Status.SUBMITTED, u)
        self.assertIn("next of kin", " ".join(cm.exception.messages))

    def test_witness_is_required_and_count_is_configurable(self):
        u = self.ready_customer(); app = self.draft(u)
        with self.assertRaises(ValidationError): services.transition(app, Status.SUBMITTED, u)
        services.add_witness(app, u, WIT); services.transition(app, Status.SUBMITTED, u)
        app2 = self.draft(u); services.add_witness(app2, u, WIT)
        with override_settings(MIN_WITNESSES=2):
            self.assertIn("at least 2 witnesses", " ".join(services.submission_problems(app2)))
            with self.assertRaises(ValidationError): services.transition(app2, Status.SUBMITTED, u)

    def test_primary_account_used_when_none_chosen_and_explicit_choice_kept(self):
        u = self.ready_customer(); other = services.add_bank_account(u, {**ACCT, "account_number": "5550001111"})
        a1 = self.draft(u); services.add_witness(a1, u, WIT); services.transition(a1, Status.SUBMITTED, u); a1.refresh_from_db()
        self.assertEqual(a1.disbursement_account, services.primary_account(u))
        a2 = self.draft(u, disbursement_account=other); services.add_witness(a2, u, WIT); services.transition(a2, Status.SUBMITTED, u); a2.refresh_from_db()
        self.assertEqual(a2.disbursement_account, other)

    def test_choosing_a_payout_account(self):
        u = self.ready_customer(); other = services.add_bank_account(u, {**ACCT, "account_number": "5550001111"}); app = self.draft(u)
        services.set_disbursement_account(app, u, other); app.refresh_from_db(); self.assertEqual(app.disbursement_account, other)
        stranger = self.ready_customer("stranger")
        with self.assertRaises(ValidationError): services.set_disbursement_account(app, u, services.primary_account(stranger))
        with self.assertRaises(PermissionDenied): services.set_disbursement_account(app, stranger, other)


class DisbursementAccountTests(Acct):
    def approved(self, user=None, **kw):
        app = self.app(**kw); self.to_manager_review(app); services.transition(app, Status.APPROVED, self.mg, "ok")
        return LoanApplication.objects.get(pk=app.pk)

    def test_disbursement_records_the_account_in_the_audit_trail(self):
        app = self.approved(); services.transition(app, Status.DISBURSED, self.lo, "TXN-9"); app.refresh_from_db()
        self.assertEqual(app.disbursement_account, services.primary_account(self.c))
        self.assertIn("paid into Centenary Bank", AuditLog.objects.filter(application=app).latest("pk").description)

    def test_loan_approved_before_accounts_existed_gets_primary_account_at_disbursement(self):
        app = self.approved(); LoanApplication.objects.filter(pk=app.pk).update(disbursement_account=None)
        services.transition(app, Status.DISBURSED, self.lo, "TXN-9"); app.refresh_from_db()
        self.assertEqual(app.disbursement_account, services.primary_account(self.c))

    def test_cannot_disburse_to_nowhere(self):
        app = self.approved(); LoanApplication.objects.filter(pk=app.pk).update(disbursement_account=None)
        BankAccount.objects.filter(user=self.c).update(is_active=False)
        with self.assertRaises(ValidationError) as cm: services.transition(app, Status.DISBURSED, self.lo, "TXN-9")
        self.assertIn("no active bank account", " ".join(cm.exception.messages)); app.refresh_from_db(); self.assertEqual(app.status, Status.APPROVED)


class AccountPageTests(Acct):
    def test_customer_page_shows_everything_staff_page_does_not(self):
        self.client.force_login(self.c); r = self.client.get("/account/")
        for text in ("Bank accounts", "Next of kin", "Personal details", "Change password", "Ready to apply?"): self.assertContains(r, text)
        self.client.force_login(self.lo); r = self.client.get("/account/")
        self.assertContains(r, "Personal details"); self.assertNotContains(r, "Bank accounts"); self.assertNotContains(r, "Next of kin")

    def test_old_profile_address_redirects(self):
        self.client.force_login(self.c); r = self.client.get("/profile/"); self.assertRedirects(r, "/account/")

    def test_profile_save_normalises_phone_and_rejects_duplicate_nin(self):
        self.client.force_login(self.c)
        self.client.post("/account/", {"phone": "0700 123 456", "nin": self.c.profile.nin}); self.c.profile.refresh_from_db()
        self.assertEqual(self.c.profile.phone, "+256700123456")
        other = self.new_customer(); self.client.force_login(other)
        r = self.client.post("/account/", {"phone": "0700123457", "nin": self.c.profile.nin})
        self.assertContains(r, "already registered"); other.profile.refresh_from_db(); self.assertNotEqual(other.profile.nin, self.c.profile.nin)

    def test_add_primary_and_remove_bank_account_through_the_page(self):
        u = self.new_customer(); self.client.force_login(u)
        r = self.client.post("/account/bank/add/", {**ACCT}, follow=True); self.assertContains(r, "Bank account added"); self.assertContains(r, "4567")
        self.assertNotContains(r, "9030001234567")  # the full number is never shown back
        self.client.post("/account/bank/add/", {**ACCT, "bank_name": "DFCU Bank", "account_number": "5550001111"})
        second = u.bank_accounts.get(account_number="5550001111")
        self.client.post(f"/account/bank/{second.pk}/primary/"); second.refresh_from_db(); self.assertTrue(second.is_primary)
        r = self.client.post(f"/account/bank/{second.pk}/remove/", follow=True); self.assertContains(r, "Bank account removed")
        self.assertEqual(services.active_accounts(u).count(), 1)

    def test_invalid_bank_details_show_errors(self):
        self.client.force_login(self.new_customer())
        r = self.client.post("/account/bank/add/", {**ACCT, "account_number": "12ab"}); self.assertContains(r, "6 to 20 digits")
        self.assertEqual(BankAccount.objects.filter(account_number="12ab").count(), 0)

    def test_add_and_remove_next_of_kin_through_the_page(self):
        u = self.new_customer(); self.client.force_login(u)
        r = self.client.post("/account/kin/add/", KIN, follow=True); self.assertContains(r, "Next of kin added"); self.assertContains(r, "+256700111222")
        r = self.client.post("/account/kin/add/", {**KIN, "phone": "nope"}); self.assertContains(r, "valid phone")
        self.client.post(f"/account/kin/{u.next_of_kin.get().pk}/remove/"); self.assertEqual(u.next_of_kin.count(), 0)

    def test_staff_cannot_use_customer_endpoints_and_customers_cannot_touch_others(self):
        self.client.force_login(self.lo)
        self.assertEqual(self.client.post("/account/bank/add/", ACCT).status_code, 403)
        self.assertEqual(self.client.post("/account/kin/add/", KIN).status_code, 403)
        other = self.new_customer(); self.client.force_login(other)
        mine = services.primary_account(self.c)
        self.assertEqual(self.client.post(f"/account/bank/{mine.pk}/remove/").status_code, 404)
        self.assertEqual(self.client.post(f"/account/kin/{self.c.next_of_kin.get().pk}/remove/").status_code, 404)

    def test_bank_removal_blocked_message_when_loan_in_progress(self):
        app = self.app(); services.transition(app, Status.SUBMITTED, self.c); self.client.force_login(self.c)
        r = self.client.post(f"/account/bank/{services.primary_account(self.c).pk}/remove/", follow=True)
        self.assertContains(r, "cannot be removed yet"); self.assertTrue(services.active_accounts(self.c).exists())

    def test_password_change(self):
        self.client.force_login(self.c); self.c.set_password("old-pass-123"); self.c.save(); self.client.force_login(self.c)
        r = self.client.post("/account/password/", {"old_password": "old-pass-123", "new_password1": "brand-new-pw-987", "new_password2": "brand-new-pw-987"}, follow=True)
        self.assertContains(r, "Your password was changed"); self.client.logout()
        self.assertTrue(self.client.login(username="customer1", password="brand-new-pw-987"))


class ApplyFlowTests(Acct):
    def test_apply_requires_a_bank_account(self):
        u = self.new_customer(); self.client.force_login(u)
        r = self.client.get(f"/apply/{self.p.pk}/", follow=True); self.assertContains(r, "Add a bank account before you apply")
        self.assertRedirects(self.client.get(f"/apply/{self.p.pk}/"), "/account/")
        r = self.client.get("/products/"); self.assertContains(r, "Add a bank account first")

    def test_apply_form_only_offers_own_accounts(self):
        self.client.force_login(self.c); stranger = self.ready_customer("stranger")
        r = self.client.get(f"/apply/{self.p.pk}/"); self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Pay the loan into"); self.assertContains(r, "Centenary Bank"); self.assertNotContains(r, "Stanbic Bank Uganda")

    def test_submit_without_witness_saves_a_draft_and_explains(self):
        self.client.force_login(self.c)
        r = self.client.post(f"/apply/{self.p.pk}/", {"branch": Branch.objects.first().pk, "amount_requested": "1000000", "purpose": "stock",
            "repayment_period": "12", "monthly_income": "2000000", "monthly_expenses": "500000", "existing_debt": "0", "submit": "1"}, follow=True)
        app = LoanApplication.objects.order_by("-pk").first()
        self.assertEqual(app.status, Status.DRAFT); self.assertEqual(app.disbursement_account, services.primary_account(self.c))
        self.assertContains(r, "Add at least 1 witness")

    def test_witness_endpoints(self):
        app = self.app(witness=False); self.client.force_login(self.c)
        r = self.client.post(f"/applications/{app.pk}/witnesses/add/", WIT, follow=True); self.assertContains(r, "Witness added"); self.assertContains(r, "Wilson Witness")
        r = self.client.post(f"/applications/{app.pk}/witnesses/add/", {**WIT, "phone": "bad"}, follow=True); self.assertContains(r, "valid phone number")
        r = self.client.post(f"/applications/{app.pk}/witnesses/add/", {**WIT, "phone": self.c.profile.phone}, follow=True); self.assertContains(r, "own loan")
        self.assertEqual(app.witnesses.count(), 1)
        for u in (self.lo, self.new_customer()):
            self.client.force_login(u)
            self.assertEqual(self.client.post(f"/applications/{app.pk}/witnesses/add/", {**WIT, "phone": "0772000009"}).status_code, 403)
            self.assertEqual(self.client.post(f"/witnesses/{app.witnesses.get().pk}/remove/").status_code, 403)
        self.client.force_login(self.c); self.client.post(f"/witnesses/{app.witnesses.get().pk}/remove/"); self.assertEqual(app.witnesses.count(), 0)

    def test_choose_payout_account_endpoint(self):
        other = services.add_bank_account(self.c, {**ACCT, "account_number": "5550001111"}); app = self.app(); self.client.force_login(self.c)
        self.client.post(f"/applications/{app.pk}/payout-account/", {"account": other.pk}); app.refresh_from_db(); self.assertEqual(app.disbursement_account, other)
        r = self.client.post(f"/applications/{app.pk}/payout-account/", {"account": 99999}, follow=True); self.assertContains(r, "Choose one of your own")

    def test_new_customer_end_to_end_through_the_website(self):
        c = self.client
        r = c.post("/register/", {"username": "fresh", "password1": "a-strong-pass-77", "password2": "a-strong-pass-77"}, follow=True)
        self.assertRedirects(r, "/account/"); self.assertContains(r, "Welcome")
        self.assertContains(c.get("/"), "Finish setting up your account")  # dashboard nudges until the account is complete
        c.post("/account/", {"phone": "0700 555 000", "nin": "FR12345678ABCD"}); c.post("/account/bank/add/", ACCT); c.post("/account/kin/add/", KIN)
        self.assertNotContains(c.get("/"), "Finish setting up your account")
        c.post(f"/apply/{self.p.pk}/", {"branch": Branch.objects.first().pk, "amount_requested": "1000000", "purpose": "stock", "repayment_period": "12",
            "monthly_income": "2000000", "monthly_expenses": "500000", "existing_debt": "0"})
        app = LoanApplication.objects.get(customer__username="fresh"); self.assertEqual(app.status, Status.DRAFT)
        r = c.get(f"/applications/{app.pk}/"); self.assertContains(r, "Before you can submit"); self.assertContains(r, "disabled")
        c.post(f"/applications/{app.pk}/witnesses/add/", WIT)
        r = c.get(f"/applications/{app.pk}/"); self.assertNotContains(r, "Before you can submit")
        c.post(f"/applications/{app.pk}/transition/", {"to": "SUBMITTED"}); app.refresh_from_db(); self.assertEqual(app.status, Status.SUBMITTED)


class VisibilityTests(Acct):
    def test_customer_sees_masked_account_staff_see_full_details(self):
        app = self.app(); services.transition(app, Status.SUBMITTED, self.c)
        number = services.primary_account(self.c).account_number
        self.client.force_login(self.c); r = self.client.get(f"/applications/{app.pk}/")
        self.assertNotContains(r, number); self.assertContains(r, number[-4:])
        self.client.force_login(self.lo); r = self.client.get(f"/applications/{app.pk}/")
        self.assertContains(r, number); self.assertContains(r, "Grace Kin"); self.assertContains(r, "Test Witness"); self.assertContains(r, "Payout account")

    def test_new_customer_sees_setup_prompts(self):
        self.client.force_login(self.new_customer())
        r = self.client.get("/"); self.assertContains(r, "Add a bank account to receive the loan"); self.assertContains(r, "Add at least one next of kin")

    def test_unread_count_in_navigation(self):
        app = self.app(); services.transition(app, Status.SUBMITTED, self.c); self.client.force_login(self.c)
        self.assertContains(self.client.get("/"), 'badge-dot">')


class WordingTests(Acct):
    BANNED = ("LOCAL TESTING ONLY", "RESEARCH PROTOTYPE", "(demo)", "(fictional)", "LOCAL DEVELOPMENT / RESEARCH ONLY", "Fictional research product")

    def test_pages_carry_no_demo_wording(self):
        app = self.app(); services.transition(app, Status.SUBMITTED, self.c)
        pages = {"login": self.client.get("/login/")}
        self.client.force_login(self.c)
        for url in ("/", "/products/", f"/applications/{app.pk}/", "/account/", "/notifications/", "/survey/", f"/apply/{self.p.pk}/"):
            pages[url] = self.client.get(url)
        for name, r in pages.items():
            self.assertEqual(r.status_code, 200, name)
            for bad in self.BANNED: self.assertNotContains(r, bad, msg_prefix=name)

    def test_branch_labels_and_setup_output_are_clean(self):
        self.assertNotIn("demo", str(Branch.objects.first()).lower())
        for f in ("setup_and_run.py", "core/management/commands/seed_data.py"):
            text = Path(f).read_text(encoding="utf-8")
            for bad in ("RESEARCH ONLY", "LOCAL DEVELOPMENT", "demo data"): self.assertNotIn(bad, text, f)

    def test_seeded_products_have_real_descriptions(self):
        self.assertFalse(LoanProduct.objects.filter(description="Fictional research product").exists())


class SeedAndUiHelperTests(Acct):
    def test_sample_customer_can_apply_straight_away_and_seed_is_idempotent(self):
        from django.core.management import call_command
        call_command("seed_data", verbosity=0)
        self.assertEqual(self.c.bank_accounts.count(), 1); self.assertEqual(self.c.next_of_kin.count(), 1)
        self.assertEqual(services.account_readiness(self.c), [])

    def test_template_filters(self):
        self.assertEqual(money(1234567.5), "1,234,567.50"); self.assertEqual(money("0"), "0.00"); self.assertEqual(money(None), None)
        self.assertEqual(status_label("DOCUMENT_REVIEW"), "Document review"); self.assertEqual(status_class("REJECTED"), "st-bad")
        self.assertEqual(status_class("UNKNOWN"), "st-draft")

    def test_stepper_states(self):
        app = self.app()
        states = lambda: [s["state"] for s in _stepper(app)]
        self.assertEqual(states()[:2], ["current", "todo"])
        app.status = Status.MANAGER_REVIEW; self.assertEqual(states(), ["done"] * 4 + ["current"] + ["todo"] * 3)
        app.status = Status.COMPLETED; self.assertEqual(states(), ["done"] * 8)
        app.status = Status.REJECTED; st = _stepper(app); self.assertEqual((st[4]["state"], st[4]["label"]), ("bad", "Rejected"))
        app.status = Status.INFORMATION_REQUIRED; self.assertEqual(_stepper(app)[2]["label"], "Info needed")
        app.status = Status.WITHDRAWN; self.assertIsNone(_stepper(app))
