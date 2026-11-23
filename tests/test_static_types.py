import mimetypes
from django.test import SimpleTestCase


class StaticMimeTypeTests(SimpleTestCase):
    def test_css_and_js_are_never_served_as_plain_text(self):
        from config import settings  # noqa: F401  (importing pins the types)
        self.assertEqual(mimetypes.guess_type("a.css")[0], "text/css")
        self.assertEqual(mimetypes.guess_type("a.js")[0], "text/javascript")
        self.assertEqual(mimetypes.guess_type("a.svg")[0], "image/svg+xml")
