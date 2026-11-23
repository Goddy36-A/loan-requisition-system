import importlib.util
import mimetypes
import os
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured
BASE_DIR = Path(__file__).resolve().parent.parent
# On some Windows PCs the registry maps .css/.js to text/plain, so Chrome refuses the stylesheets and scripts (the admin and
# Bootstrap then show completely unstyled). Pin the correct types so static files always load.
for _ext, _type in {".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript", ".svg": "image/svg+xml",
                    ".woff": "font/woff", ".woff2": "font/woff2", ".json": "application/json"}.items():
    mimetypes.add_type(_type, _ext, True)
# add_type() alone can still lose to a bad Windows registry entry, so also make guess_type() trust these extensions first.
_PINNED = {_e: _t for _e, _t in {".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript", ".svg": "image/svg+xml",
           ".woff": "font/woff", ".woff2": "font/woff2", ".json": "application/json"}.items()}
_orig_guess_type = mimetypes.guess_type
def _guess_type(url, strict=True):
    _p = os.fspath(url).lower().split("?", 1)[0]
    for _e, _t in _PINNED.items():
        if _p.endswith(_e): return _t, None
    return _orig_guess_type(url, strict)
if mimetypes.guess_type is _orig_guess_type: mimetypes.guess_type = _guess_type
env = BASE_DIR / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
def _flag(name, default="0"): return os.environ.get(name, default) == "1"
def _csv(name, default=""): return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]

# Two modes. LOCAL MODE (the default: one command, this computer only) keeps the sample sign-ins and friendly settings.
# Anything reachable from other computers, or with DEBUG off, is HOSTED and must be configured properly (see README).
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
ALLOWED_HOSTS = _csv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost")
HOSTED = any(h not in _LOCAL_HOSTS for h in ALLOWED_HOSTS)
DEBUG = os.environ.get("DJANGO_DEBUG", "0" if HOSTED else "1") == "1"
if HOSTED and DEBUG:
    raise ImproperlyConfigured("DJANGO_DEBUG=1 is not allowed when DJANGO_ALLOWED_HOSTS lists a public host: debug pages leak "
                               "settings and code. Set DJANGO_DEBUG=0.")
LOCAL_MODE = DEBUG and not HOSTED

_DEV_KEY = "dev-only-insecure-key-local-research"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "")
if LOCAL_MODE:
    if SECRET_KEY in ("", "change-me"): SECRET_KEY = _DEV_KEY
elif SECRET_KEY in ("", "change-me", _DEV_KEY) or len(SECRET_KEY) < 32:
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set to a long random value (32+ characters) outside local mode. Generate one "
                               "with:  python -c \"import secrets; print(secrets.token_urlsafe(50))\"")
INSTALLED_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "core"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware"]
if not LOCAL_MODE:  # hosted: the development server's static handler is not available, so WhiteNoise serves the files
    if importlib.util.find_spec("whitenoise") is None:
        raise ImproperlyConfigured("WhiteNoise is required when hosting. Install it with: pip install -r requirements-cloud.txt")
    MIDDLEWARE.insert(1, "whitenoise.middleware.WhiteNoiseMiddleware")
    STORAGES = {"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                "staticfiles": {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"}}
    _https = not _flag("DJANGO_ALLOW_HTTP")  # set DJANGO_ALLOW_HTTP=1 only for a trial without HTTPS
    SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT = _https
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "3600" if _https else "0"))  # raise once HTTPS is proven
    SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_PRELOAD = False
    CSRF_TRUSTED_ORIGINS = _csv("DJANGO_CSRF_TRUSTED_ORIGINS")
    if _flag("DJANGO_BEHIND_PROXY"): SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
BEHIND_PROXY = _flag("DJANGO_BEHIND_PROXY")  # trust X-Forwarded-For (last hop) for the client address in sign-in throttling
STATIC_ROOT = BASE_DIR / "staticfiles"  # `python manage.py collectstatic` target when hosting

# Sign-in throttling: after this many wrong attempts for one username from one address (or LOGIN_USER_MAX_FAILURES for the
# username from anywhere) the sign-in is locked for LOGIN_LOCK_MINUTES. Gentler on this computer, stricter when hosted.
LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", "10" if LOCAL_MODE else "5"))
LOGIN_LOCK_MINUTES = int(os.environ.get("LOGIN_LOCK_MINUTES", "1" if LOCAL_MODE else "15"))
LOGIN_USER_MAX_FAILURES = int(os.environ.get("LOGIN_USER_MAX_FAILURES", "50" if LOCAL_MODE else "25"))

ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request",
    "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages",
    "core.context.ui"]}}]
WSGI_APPLICATION = "config.wsgi.application"
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
TIME_ZONE = "Africa/Kampala"; USE_TZ = True
STATIC_URL = "static/"; MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOGIN_URL = "login"; LOGIN_REDIRECT_URL = "dashboard"; LOGOUT_REDIRECT_URL = "login"
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
(BASE_DIR / "logs").mkdir(exist_ok=True)
LOGGING = {"version": 1, "handlers": {"f": {"class": "logging.FileHandler", "filename": BASE_DIR / "logs" / "application.log"}},
    "root": {"handlers": ["f"], "level": "INFO"}}

# Repayment policy (override in .env). The late fee is charged ONCE per installment, as a percentage of the
# unpaid installment amount, once it is more than LATE_FEE_GRACE_DAYS past its due date.
LATE_FEE_PERCENT = os.environ.get("LATE_FEE_PERCENT", "5")
LATE_FEE_GRACE_DAYS = int(os.environ.get("LATE_FEE_GRACE_DAYS", "3"))
REMINDER_DAYS_BEFORE = int(os.environ.get("REMINDER_DAYS_BEFORE", "3"))

# Loan applications need this many witnesses (and may have at most MAX_WITNESSES).
MIN_WITNESSES = int(os.environ.get("MIN_WITNESSES", "1"))
MAX_WITNESSES = int(os.environ.get("MAX_WITNESSES", "3"))

from django.contrib.messages import constants as _m
MESSAGE_TAGS = {_m.ERROR: "danger"}  # so messages.error renders as a red Bootstrap alert
