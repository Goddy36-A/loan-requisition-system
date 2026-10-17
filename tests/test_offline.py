"""The website must work with no internet access: no CDN links, Bootstrap served from the project, tolerant setup script."""
import re
import subprocess
import sys
from unittest import mock
from django.contrib.staticfiles import finders
from core import services
from core.models import *
from tests.test_hardening import Base

ASSETS = {"core/vendor/bootstrap/bootstrap.min.css": (200_000, "Bootstrap  v5.3.3"),
          "core/vendor/bootstrap/bootstrap.bundle.min.js": (70_000, "Bootstrap v5.3.3")}


class OfflineTests(Base):
    def test_bootstrap_is_bundled_with_the_project(self):
        for name, (min_size, banner) in ASSETS.items():
            path = finders.find(name); self.assertIsNotNone(path, name)
            text = open(path, encoding="utf-8").read()
            self.assertGreater(len(text), min_size, name); self.assertIn(banner, text, name)
            self.assertNotIn("sourceMappingURL", text, name)  # would trigger a request for a file we do not ship
        self.assertIsNotNone(finders.find("core/vendor/bootstrap/LICENSE"))

    def test_pages_reference_only_local_resources(self):
        app = self.app(); services.transition(app, Status.SUBMITTED, self.c)
        self.client.force_login(self.c); seen = 0
        pages = ["/login/", "/", "/products/", "/account/", f"/applications/{app.pk}/", f"/apply/{self.p.pk}/", "/notifications/", "/survey/", "/account/password/"]
        for url in pages:
            html = self.client.get(url, follow=True).content.decode()
            self.assertIn("/static/core/vendor/bootstrap/bootstrap.min.css", html, url)
            self.assertIn("/static/core/vendor/bootstrap/bootstrap.bundle.min.js", html, url)
            self.assertEqual(re.findall(r"https?://[^\s\"'<>)]+", html), [], url)  # no CDN, font or analytics hosts
            self.assertEqual(re.findall(r"@import|url\(\s*[\"']?//", html), [], url)
            seen += 1
        self.assertEqual(seen, len(pages))

    def test_staff_pages_are_offline_too(self):
        for u in (self.lo, self.mg):
            self.client.force_login(u)
            for url in ("/", "/overdue/", "/account/"):
                self.assertEqual(re.findall(r"https?://[^\s\"'<>)]+", self.client.get(url).content.decode()), [], (u.username, url))


class SetupScriptTests(Base):
    def setUp(self):
        super().setUp()
        import setup_and_run; self.mod = setup_and_run

    def test_install_works_when_pip_succeeds(self):
        with mock.patch.object(self.mod, "run") as run: self.mod.install_requirements()
        self.assertIn("--disable-pip-version-check", run.call_args.args)

    def test_offline_restart_continues_when_django_is_already_installed(self):
        fail = subprocess.CalledProcessError(1, "pip")
        with mock.patch.object(self.mod, "run", side_effect=fail), mock.patch.object(self.mod, "VPY", sys.executable):  # this interpreter has Django
            self.mod.install_requirements()  # must not raise

    def test_first_run_offline_gives_a_clear_message(self):
        fail = subprocess.CalledProcessError(1, "pip")
        with mock.patch.object(self.mod, "run", side_effect=fail), \
             mock.patch.object(self.mod.subprocess, "run", return_value=mock.Mock(returncode=1)):
            with self.assertRaises(SystemExit) as cm: self.mod.install_requirements()
        self.assertIn("internet connection", str(cm.exception))
