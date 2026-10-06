import os
from pathlib import Path
BASE_DIR = Path(__file__).resolve().parent.parent
env = BASE_DIR / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1); os.environ.setdefault(k.strip(), v.strip())
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key-local-research")
DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
INSTALLED_APPS = ["django.contrib.admin", "django.contrib.auth", "django.contrib.contenttypes",
    "django.contrib.sessions", "django.contrib.messages", "django.contrib.staticfiles", "core"]
MIDDLEWARE = ["django.middleware.security.SecurityMiddleware", "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware", "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF = "config.urls"
TEMPLATES = [{"BACKEND": "django.template.backends.django.DjangoTemplates", "DIRS": [], "APP_DIRS": True,
    "OPTIONS": {"context_processors": ["django.template.context_processors.request",
    "django.contrib.auth.context_processors.auth", "django.contrib.messages.context_processors.messages"]}}]
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
