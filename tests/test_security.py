"""Local vs hosted mode, sample-password guard, seed modes, sign-in throttling and the sample-accounts box."""
import io
import json
import os
import re
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from unittest import mock
from django.contrib import admin
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone
from core import security, throttle
from core.forms import ThrottledLoginMixin
from core.models import *
from core.sample_accounts import ACCOUNTS, PUBLISHED_PASSWORDS

BASE = Path(__file__).resolve().parent.parent
GOOD_KEY = "k" * 10 + "Zq8-Lm3_Vw9Xb2Nc5Ty7Ud1Fh4Gj6" + "p" * 10  # 50 chars
PROBE = r"""
import json, os, sys, pathlib
_ex = pathlib.Path.exists
pathlib.Path.exists = lambda self: False if self.name == ".env" else _ex(self)   # ignore any developer .env
if os.environ.pop("_NO_WHITENOISE", ""): sys.modules["whitenoise"] = None
sys.path.insert(0, %r); os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"
from django.conf import settings
g = lambda n, d=None: getattr(settings, n, d)
print(json.dumps({k: g(k) for k in ("LOCAL_MODE", "DEBUG", "HOSTED", "SECRET_KEY", "SESSION_COOKIE_SECURE", "CSRF_COOKIE_SECURE",
    "SECURE_SSL_REDIRECT", "SECURE_HSTS_SECONDS", "SECURE_PROXY_SSL_HEADER", "LOGIN_MAX_FAILURES", "LOGIN_LOCK_MINUTES",
    "LOGIN_USER_MAX_FAILURES", "BEHIND_PROXY", "CSRF_TRUSTED_ORIGINS")} | {"MIDDLEWARE": g("MIDDLEWARE")}))
""" % str(BASE)


def settings_for(**env):
    """Import config.settings in a fresh interpreter with exactly this environment. Returns (settings dict, error text)."""
    clean = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "LOGIN_", "SEED_"))}
    r = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, env={**clean, **env}, cwd=BASE)
    return (json.loads(r.stdout), "") if r.returncode == 0 else (None, r.stderr)


class SettingsModeTests(TestCase):
    HOST = {"DJANGO_ALLOWED_HOSTS": "app.example.com"}

    def test_default_is_local_mode_exactly_as_before(self):
        s, err = settings_for(); self.assertEqual(err, "")
        self.assertEqual((s["LOCAL_MODE"], s["DEBUG"], s["HOSTED"]), (True, True, False))
        self.assertEqual(s["SECRET_KEY"], "dev-only-insecure-key-local-research")
        self.assertEqual((s["LOGIN_MAX_FAILURES"], s["LOGIN_LOCK_MINUTES"]), (10, 1))  # gentle: students forget passwords
        self.assertFalse(s["SESSION_COOKIE_SECURE"] or s["SECURE_SSL_REDIRECT"]); self.assertFalse(any("whitenoise" in m for m in s["MIDDLEWARE"]))

    def test_local_mode_ignores_the_placeholder_key_from_env_example(self):
        s, _ = settings_for(DJANGO_SECRET_KEY="change-me", DJANGO_DEBUG="1"); self.assertEqual(s["SECRET_KEY"], "dev-only-insecure-key-local-research")

    def test_hosting_requires_a_real_secret_key(self):
        for key in (None, "", "change-me", "dev-only-insecure-key-local-research", "short-but-not-empty"):
            env = {**self.HOST, **({} if key is None else {"DJANGO_SECRET_KEY": key})}
            s, err = settings_for(**env); self.assertIsNone(s, key); self.assertIn("DJANGO_SECRET_KEY", err)

    def test_debug_is_refused_when_hosted(self):
        s, err = settings_for(**self.HOST, DJANGO_SECRET_KEY=GOOD_KEY, DJANGO_DEBUG="1"); self.assertIsNone(s); self.assertIn("DJANGO_DEBUG=1 is not allowed", err)

    def test_hosted_settings_are_hardened(self):
        s, err = settings_for(**self.HOST, DJANGO_SECRET_KEY=GOOD_KEY, DJANGO_CSRF_TRUSTED_ORIGINS="https://app.example.com"); self.assertEqual(err, "")
        self.assertEqual((s["LOCAL_MODE"], s["DEBUG"], s["HOSTED"]), (False, False, True)); self.assertEqual(s["SECRET_KEY"], GOOD_KEY)
        self.assertTrue(s["SESSION_COOKIE_SECURE"] and s["CSRF_COOKIE_SECURE"] and s["SECURE_SSL_REDIRECT"])
        self.assertEqual(s["SECURE_HSTS_SECONDS"], 3600); self.assertEqual((s["LOGIN_MAX_FAILURES"], s["LOGIN_LOCK_MINUTES"]), (5, 15))
        self.assertEqual(s["CSRF_TRUSTED_ORIGINS"], ["https://app.example.com"]); self.assertIn("whitenoise.middleware.WhiteNoiseMiddleware", s["MIDDLEWARE"])

    def test_http_only_trial_and_proxy_options(self):
        s, _ = settings_for(**self.HOST, DJANGO_SECRET_KEY=GOOD_KEY, DJANGO_ALLOW_HTTP="1")
        self.assertFalse(s["SESSION_COOKIE_SECURE"] or s["SECURE_SSL_REDIRECT"]); self.assertEqual(s["SECURE_HSTS_SECONDS"], 0)
        s, _ = settings_for(**self.HOST, DJANGO_SECRET_KEY=GOOD_KEY, DJANGO_BEHIND_PROXY="1")
        self.assertEqual(s["SECURE_PROXY_SSL_HEADER"], ["HTTP_X_FORWARDED_PROTO", "https"]); self.assertTrue(s["BEHIND_PROXY"])

    def test_debug_off_on_localhost_is_also_treated_as_hosted_mode(self):
        s, err = settings_for(DJANGO_DEBUG="0"); self.assertIsNone(s); self.assertIn("DJANGO_SECRET_KEY", err)
        s, err = settings_for(DJANGO_DEBUG="0", DJANGO_SECRET_KEY=GOOD_KEY); self.assertEqual(err, ""); self.assertFalse(s["LOCAL_MODE"])

    def test_hosting_without_whitenoise_says_what_to_install(self):
        s, err = settings_for(**self.HOST, DJANGO_SECRET_KEY=GOOD_KEY, _NO_WHITENOISE="1"); self.assertIsNone(s); self.assertIn("requirements-cloud.txt", err)

    def test_environment_overrides_for_throttling(self):
        s, _ = settings_for(LOGIN_MAX_FAILURES="3", LOGIN_LOCK_MINUTES="30"); self.assertEqual((s["LOGIN_MAX_FAILURES"], s["LOGIN_LOCK_MINUTES"]), (3, 30))


class SeedModeTests(TestCase):
    def seed(self, *args, env=None):
        out = io.StringIO()
        with mock.patch.dict(os.environ, env or {}): call_command("seed_data", *args, stdout=out)
        return out.getvalue()

    def printed(self, text): return dict(re.findall(r"^\s{2}(\w+)\s+(\S+)$", text, re.M))

    def test_local_mode_uses_the_published_passwords_and_reset_restores_them(self):
        self.seed()
        for name, pw in PUBLISHED_PASSWORDS.items(): self.assertIsNotNone(authenticate(username=name, password=pw), name)
        u = User.objects.get(username="customer1"); u.set_password("I-changed-it-123"); u.save()
        self.assertIn("reset to the published ones", self.seed("--reset-passwords"))
        self.assertIsNotNone(authenticate(username="customer1", password="Customer@12345"))

    def test_seeding_twice_does_not_touch_existing_passwords(self):
        self.seed(); u = User.objects.get(username="manager"); u.set_password("Mine-12345-xyz"); u.save(); self.seed()
        self.assertIsNotNone(authenticate(username="manager", password="Mine-12345-xyz"))

    @override_settings(LOCAL_MODE=False)
    def test_hosted_mode_generates_random_passwords_printed_once(self):
        text = self.seed(); shown = self.printed(text)
        self.assertEqual(set(shown), set(PUBLISHED_PASSWORDS)); self.assertEqual(len(set(shown.values())), 6)
        for name, pw in shown.items():
            self.assertIsNotNone(authenticate(username=name, password=pw), name); self.assertGreaterEqual(len(pw), 12)
            self.assertIsNone(authenticate(username=name, password=PUBLISHED_PASSWORDS[name]), name)
        self.assertNotIn("New passwords", self.seed())  # second run: nothing new to print, nothing changed
        self.assertIsNotNone(authenticate(username="admin", password=shown["admin"]))

    @override_settings(LOCAL_MODE=False)
    def test_hosted_mode_can_use_chosen_passwords_without_printing_them(self):
        text = self.seed(env={"SEED_PASSWORD_ADMIN": "My-own-Admin-Pass-2026"})
        self.assertNotIn("My-own-Admin-Pass-2026", text); self.assertNotIn("admin ", " ".join(self.printed(text)) + " ")
        self.assertIsNotNone(authenticate(username="admin", password="My-own-Admin-Pass-2026"))

    @override_settings(LOCAL_MODE=False)
    def test_hosted_mode_refuses_the_published_password_as_a_choice(self):
        with self.assertRaises(CommandError): self.seed(env={"SEED_PASSWORD_ADMIN": "Admin@12345"})

    @override_settings(LOCAL_MODE=False)
    def test_reset_passwords_rotates_when_hosted(self):
        first = self.printed(self.seed()); second = self.printed(self.seed("--reset-passwords"))
        self.assertEqual(set(second), set(first)); self.assertTrue(all(first[n] != second[n] for n in first))
        self.assertIsNone(authenticate(username="admin", password=first["admin"])); self.assertIsNotNone(authenticate(username="admin", password=second["admin"]))

    def test_sample_customer_is_still_ready_to_apply(self):
        self.seed(); u = User.objects.get(username="customer1"); self.assertEqual((u.bank_accounts.count(), u.next_of_kin.count()), (1, 1))


class StartupGuardTests(TestCase):
    def setUp(self): call_command("seed_data", stdout=io.StringIO())  # sample accounts with their published passwords

    def test_local_mode_is_never_blocked(self):
        from core.checks import sample_passwords_in_use
        self.assertTrue(security.accounts_with_published_passwords())  # sample passwords are in use...
        security.assert_safe_to_serve()  # ...which is fine on this computer
        self.assertEqual(sample_passwords_in_use(None), [])

    @override_settings(LOCAL_MODE=False)
    def test_hosted_with_published_passwords_is_refused(self):
        self.assertCountEqual(security.accounts_with_published_passwords(), list(PUBLISHED_PASSWORDS))
        with self.assertRaises(ImproperlyConfigured) as cm: security.assert_safe_to_serve()
        self.assertIn("seed_data --reset-passwords", str(cm.exception))
        from core.checks import sample_passwords_in_use
        errors = sample_passwords_in_use(None); self.assertEqual([e.id for e in errors], ["core.E001"])

    @override_settings(LOCAL_MODE=False)
    def test_only_the_accounts_still_on_published_passwords_are_listed(self):
        for n in ("admin", "manager"):
            u = User.objects.get(username=n); u.set_password("Changed-pass-123"); u.save()
        self.assertCountEqual(security.accounts_with_published_passwords(), ["customer1", "loanofficer", "creditofficer", "researcher"])

    @override_settings(LOCAL_MODE=False)
    def test_resetting_passwords_clears_the_problem(self):
        call_command("seed_data", "--reset-passwords", stdout=io.StringIO())
        security.assert_safe_to_serve()
        from core.checks import sample_passwords_in_use
        self.assertEqual(sample_passwords_in_use(None), [])

    @override_settings(LOCAL_MODE=False)
    def test_migrate_is_allowed_to_run_so_an_old_database_can_catch_up(self):
        from core.checks import sample_passwords_in_use
        with mock.patch.object(sys, "argv", ["manage.py", "migrate"]): self.assertEqual(sample_passwords_in_use(None), [])
        with mock.patch.object(sys, "argv", ["manage.py", "runserver"]): self.assertEqual(len(sample_passwords_in_use(None)), 1)

    @override_settings(LOCAL_MODE=False)
    def test_a_user_who_is_not_a_sample_account_is_not_flagged(self):
        User.objects.all().delete(); User.objects.create_user("admin2", password="Admin@12345")
        self.assertEqual(security.accounts_with_published_passwords(), [])

    def test_seed_command_can_run_even_when_checks_would_fail(self):
        from core.management.commands.seed_data import Command
        self.assertEqual(Command.requires_system_checks, [])


@override_settings(LOGIN_MAX_FAILURES=3, LOGIN_LOCK_MINUTES=15, LOGIN_USER_MAX_FAILURES=6)
class ThrottleTests(TestCase):
    def setUp(self): call_command("seed_data", stdout=io.StringIO())

    def attempt(self, user="customer1", pw="wrong-password", ip="10.0.0.1", url="/login/"):
        return self.client.post(url, {"username": user, "password": pw}, REMOTE_ADDR=ip)

    def logged_in(self, r): return "_auth_user_id" in self.client.session

    def test_locks_after_repeated_wrong_passwords_even_for_the_right_password(self):
        for _ in range(3): self.assertContains(self.attempt(), "Please enter a correct")
        r = self.attempt(pw="Customer@12345"); self.assertContains(r, "Too many failed sign-in attempts"); self.assertContains(r, "15 minutes"); self.assertFalse(self.logged_in(r))

    def test_other_accounts_and_other_addresses_are_not_affected(self):
        for _ in range(3): self.attempt()
        self.assertTrue(self.logged_in(self.attempt("loanofficer", "LoanOfficer@12345")))  # different username, same address
        self.client.logout()
        self.assertTrue(self.logged_in(self.attempt("customer1", "Customer@12345", ip="10.9.9.9")))  # same username, different address

    def test_distributed_guessing_against_one_username_is_stopped(self):
        for i in range(6): self.attempt(ip=f"10.1.0.{i}")  # a different address each time
        r = self.attempt("customer1", "Customer@12345", ip="10.1.0.99"); self.assertContains(r, "Too many failed"); self.assertFalse(self.logged_in(r))

    def test_unknown_usernames_get_the_same_treatment_and_message(self):
        for _ in range(3): self.attempt("no-such-person")
        r = self.attempt("no-such-person"); self.assertContains(r, "Too many failed sign-in attempts")
        real = self.attempt(); self.assertContains(real, "Please enter a correct")  # a real user's first failure reads the same as a fake one's

    def test_success_resets_the_counter(self):
        for _ in range(2): self.attempt()
        self.assertTrue(self.logged_in(self.attempt(pw="Customer@12345"))); self.client.logout()
        for _ in range(2): self.assertContains(self.attempt(), "Please enter a correct")  # would be locked if the earlier failures still counted

    def test_lock_expires(self):
        for _ in range(3): self.attempt()
        LoginAttempt.objects.update(locked_until=timezone.now() - timedelta(seconds=1))
        self.assertTrue(self.logged_in(self.attempt(pw="Customer@12345"))); self.assertEqual(LoginAttempt.objects.count(), 0)

    def test_old_failures_age_out(self):
        self.attempt(); self.attempt()
        LoginAttempt.objects.update(first_failure_at=timezone.now() - timedelta(minutes=16))
        self.attempt(); self.assertEqual(LoginAttempt.objects.get(key__startswith="user-ip:").failures, 1)

    def test_lock_is_written_to_the_audit_log_once(self):
        for _ in range(3): self.attempt()  # the third wrong attempt locks this username from this address
        rows = AuditLog.objects.filter(action="Sign-in locked"); self.assertEqual(rows.count(), 1)
        self.assertIn("customer1", rows[0].description); self.assertNotIn("wrong-password", rows[0].description)
        before = list(LoginAttempt.objects.order_by("key").values_list("key", "failures", "locked_until"))
        for _ in range(4): self.attempt(pw="Customer@12345")  # attempts made while locked are refused...
        self.assertEqual(AuditLog.objects.filter(action="Sign-in locked").count(), 1)  # ...without piling up more rows
        self.assertEqual(list(LoginAttempt.objects.order_by("key").values_list("key", "failures", "locked_until")), before)  # or extending the lock

    def test_admin_sign_in_cannot_be_used_to_get_around_the_lock(self):
        for _ in range(3): self.attempt("admin", url="/admin/login/?next=/admin/")
        r = self.attempt("admin", "Admin@12345", url="/admin/login/?next=/admin/"); self.assertContains(r, "Too many failed"); self.assertNotIn("_auth_user_id", self.client.session)
        r = self.attempt("admin", "Admin@12345"); self.assertContains(r, "Too many failed")  # the website sign-in shares the same lock
        self.assertTrue(issubclass(admin.site.login_form, ThrottledLoginMixin))

    def test_unlock_helpers(self):
        for _ in range(3): self.attempt()
        self.assertGreater(throttle.unlock("CUSTOMER1"), 0)  # case-insensitive
        self.assertTrue(self.logged_in(self.attempt(pw="Customer@12345"))); self.client.logout()
        for _ in range(3): self.attempt("manager")
        out = io.StringIO(); call_command("unlock_logins", stdout=out); self.assertIn("Cleared", out.getvalue()); self.assertEqual(LoginAttempt.objects.count(), 0)

    def test_unlock_action_in_admin(self):
        for _ in range(3): self.attempt()
        self.client.force_login(User.objects.get(username="admin"))
        ids = list(LoginAttempt.objects.values_list("pk", flat=True))
        self.client.post("/admin/core/loginattempt/", {"action": "unlock", "_selected_action": ids}, follow=True)
        self.assertEqual(LoginAttempt.objects.count(), 0)

    def test_client_address_handling(self):
        from django.test import RequestFactory
        req = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="6.6.6.6, 1.2.3.4", REMOTE_ADDR="10.0.0.9")
        self.assertEqual(throttle.client_ip(req), "10.0.0.9")  # header ignored unless we run behind a proxy
        with override_settings(BEHIND_PROXY=True): self.assertEqual(throttle.client_ip(req), "1.2.3.4")  # the proxy's own entry, not a forged one
        huge = RequestFactory().get("/", HTTP_X_FORWARDED_FOR="9" * 500, REMOTE_ADDR="10.0.0.9")
        with override_settings(BEHIND_PROXY=True): ip = throttle.client_ip(huge)
        self.assertLessEqual(len(ip), 45); self.assertLessEqual(len(throttle._keys("u" * 300, ip)[0][0]), 220)  # keys always fit the column

    def test_throttle_does_not_get_in_the_way_of_normal_use(self):
        for u, pw, _ in ACCOUNTS:
            self.assertTrue(self.logged_in(self.attempt(u, pw))); self.client.logout()


class LoginPageHidesPasswordsTests(TestCase):
    def setUp(self): call_command("seed_data", stdout=io.StringIO())

    def test_login_page_never_shows_passwords_on_this_computer(self):
        r = self.client.get("/login/"); self.assertNotContains(r, "Sign-in accounts")
        for name, pw, _ in ACCOUNTS: self.assertNotContains(r, f"<code>{pw}</code>"); self.assertNotContains(r, pw)

    @override_settings(LOCAL_MODE=False)
    def test_never_shown_when_hosted(self):
        r = self.client.get("/login/"); self.assertNotContains(r, "Sign-in accounts"); self.assertNotContains(r, "Customer@12345")

    def test_setup_script_prints_the_same_passwords(self):
        text = (BASE / "setup_and_run.py").read_text(encoding="utf-8")
        for name, pw, _ in ACCOUNTS: self.assertIn(f"{name}/{pw}", text)
