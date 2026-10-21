import os
from django.core.wsgi import get_wsgi_application
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
application = get_wsgi_application()
from core.security import assert_safe_to_serve  # noqa: E402
assert_safe_to_serve()  # hosted + published sample passwords = refuse to start
