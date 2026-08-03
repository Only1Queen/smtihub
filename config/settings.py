"""SMTI HUB settings.

Deploy-safe defaults live here from day one rather than in a later hardening
phase: the secure flags below are five lines, and scheduling them for "later" is
how they get shipped without.
"""

import os
import sys
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Tests run with DEBUG=False so they exercise production settings, but the test
# client speaks http — without this every request 301s to https.
TESTING = "test" in sys.argv


def env_bool(name, default=False):
    return os.environ.get(name, str(int(default))).strip().lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-only-insecure-key")
DEBUG = env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
# The container healthcheck calls /healthz on the loopback, where the Host
# header is the address, not the site name — without these it gets a 400 and
# reports a healthy container unhealthy.
ALLOWED_HOSTS += [h for h in ("localhost", "127.0.0.1") if h not in ALLOWED_HOSTS]

# Same reasoning as the DATABASE_URL check below: a placeholder that quietly
# works is how it reaches production. Every session cookie and password-reset
# link in the system is signed with this.
if not DEBUG and not TESTING and SECRET_KEY in {"dev-only-insecure-key", "build-only"}:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY is not set (or is still the placeholder).\n"
        "  Generate one: python3 -c \"import secrets; print(secrets.token_urlsafe(64))\"\n"
        "See DEPLOYMENT.md."
    )

# Unhandled 500s go here as well as to the container log, which nobody reads
# until someone complains. Format: "Name <addr>,Name <addr>".
ADMINS = [tuple(p.strip(" >").split(" <")) for p in os.environ.get("DJANGO_ADMINS", "").split(",")
          if " <" in p]
SERVER_EMAIL = os.environ.get("SERVER_EMAIL", "smti-hub@localhost")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "axes",
    "hub",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    # Must be last: it turns the lockout raised by AxesBackend into a response.
    "axes.middleware.AxesMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
        "hub.context.hub_context",
    ]},
}]

# PostgreSQL only. There is no SQLite fallback on purpose: the audit table's
# append-only guarantee rests on Postgres GRANTs (see deploy/grants.sql), and a
# fallback that silently works in dev but not in production is how that kind of
# rule gets discovered too late.
DATABASE_URL = os.environ.get("DATABASE_URL", "")
if not DATABASE_URL:
    raise ImproperlyConfigured(
        "DATABASE_URL is not set. SMTI HUB requires PostgreSQL.\n"
        "  Docker:     it is set for you by docker-compose.yml\n"
        "  Local dev:  export DATABASE_URL=postgres://smti_app:PASSWORD@localhost:5432/smti\n"
        "See DEPLOYMENT.md."
    )

_url = urlparse(DATABASE_URL)
if _url.scheme not in {"postgres", "postgresql"}:
    raise ImproperlyConfigured(
        f"DATABASE_URL must be a postgres:// URL, got '{_url.scheme}://'. "
        "SMTI HUB does not support other databases."
    )

DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": _url.path.lstrip("/"),
    "USER": _url.username,
    "PASSWORD": _url.password,
    "HOST": _url.hostname,
    "PORT": _url.port or 5432,
    "CONN_MAX_AGE": 0 if TESTING else 60,
    "OPTIONS": {"connect_timeout": 10},
}}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
     "OPTIONS": {"min_length": 12}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# --- Authentication --------------------------------------------------------
# Order matters. Axes first so a locked-out attempt is refused before any
# password is checked; ModelBackend last so the break-glass local superuser
# still works when Active Directory is unreachable.
AUTH_LDAP_SERVER_URI = os.environ.get("AUTH_LDAP_SERVER_URI", "")
LDAP_ENABLED = bool(AUTH_LDAP_SERVER_URI)

AUTHENTICATION_BACKENDS = (
    ["axes.backends.AxesBackend"]
    + (["django_auth_ldap.backend.LDAPBackend"] if LDAP_ENABLED else [])
    + ["django.contrib.auth.backends.ModelBackend"]
)

if LDAP_ENABLED:
    import ldap
    from django_auth_ldap.config import LDAPSearch, NestedGroupOfNamesType

    AUTH_LDAP_BIND_DN = os.environ.get("AUTH_LDAP_BIND_DN", "")
    AUTH_LDAP_BIND_PASSWORD = os.environ.get("AUTH_LDAP_BIND_PASSWORD", "")

    # `(!(userAccountControl:1.2.840.113556.1.4.803:=2))` is AD's "not disabled".
    # Without it a disabled account keeps signing in here after IT has closed it.
    AUTH_LDAP_USER_SEARCH = LDAPSearch(
        os.environ["AUTH_LDAP_USER_SEARCH_BASE"], ldap.SCOPE_SUBTREE,
        os.environ.get("AUTH_LDAP_USER_FILTER",
                       "(&(objectClass=user)(sAMAccountName=%(user)s)"
                       "(!(userAccountControl:1.2.840.113556.1.4.803:=2)))"),
    )
    AUTH_LDAP_GROUP_SEARCH = LDAPSearch(
        os.environ.get("AUTH_LDAP_GROUP_SEARCH_BASE", os.environ["AUTH_LDAP_USER_SEARCH_BASE"]),
        ldap.SCOPE_SUBTREE, "(objectClass=group)",
    )
    # Nested, because "SMTI-Managers" containing a role group is how AD is
    # actually administered and a flat lookup would silently miss those people.
    AUTH_LDAP_GROUP_TYPE = NestedGroupOfNamesType(name_attr="cn")

    AUTH_LDAP_USER_ATTR_MAP = {"first_name": "givenName", "last_name": "sn", "email": "mail"}
    AUTH_LDAP_ALWAYS_UPDATE_USER = True
    AUTH_LDAP_CACHE_TIMEOUT = 3600  # group memberships, not credentials

    # Optional: only members of this group may sign in at all.
    AUTH_LDAP_REQUIRE_GROUP = os.environ.get("AUTH_LDAP_ACCESS_GROUP_DN") or None
    # Membership of this group grants the manager role (see hub/signals.py).
    AUTH_LDAP_MANAGER_GROUP_DN = os.environ.get("AUTH_LDAP_MANAGER_GROUP_DN", "")

    AUTH_LDAP_CONNECTION_OPTIONS = {
        ldap.OPT_REFERRALS: 0,          # AD returns referrals python-ldap cannot chase
        ldap.OPT_NETWORK_TIMEOUT: 10,
    }
    # ldap:// on 389 is cleartext; StartTLS upgrades it. ldaps:// is already TLS.
    AUTH_LDAP_START_TLS = env_bool("AUTH_LDAP_START_TLS", not AUTH_LDAP_SERVER_URI.startswith("ldaps://"))
else:
    AUTH_LDAP_MANAGER_GROUP_DN = ""

# --- Login lockout (django-axes) -------------------------------------------
AXES_ENABLED = not TESTING  # the lockout tests turn it back on explicitly
AXES_FAILURE_LIMIT = int(os.environ.get("AXES_FAILURE_LIMIT", 5))
AXES_COOLOFF_TIME = timedelta(minutes=int(os.environ.get("AXES_COOLOFF_MINUTES", 15)))
# Two independent locks: an IP hammering many usernames, and a username being
# hammered from many IPs. Either alone leaves the other attack open.
AXES_LOCKOUT_PARAMETERS = ["ip_address", "username"]
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_TEMPLATE = "registration/lockout.html"
# Behind nginx every request comes from the proxy, so the real client address
# has to be read from the forwarded header — one hop, ours.
AXES_IPWARE_PROXY_COUNT = None if DEBUG else 1
AXES_IPWARE_META_PRECEDENCE_ORDER = ["HTTP_X_FORWARDED_FOR", "REMOTE_ADDR"]

LANGUAGE_CODE = "en-gb"
TIME_ZONE = os.environ.get("DJANGO_TIME_ZONE", "Africa/Lagos")
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    # The manifest backend needs collectstatic to have run; tests and runserver
    # have not, so they use the plain one.
    "staticfiles": {"BACKEND": (
        "django.contrib.staticfiles.storage.StaticFilesStorage" if (DEBUG or TESTING)
        else "whitenoise.storage.CompressedManifestStaticFilesStorage")},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "team"
LOGOUT_REDIRECT_URL = "login"

# --- Email -----------------------------------------------------------------
EMAIL_BACKEND = ("django.core.mail.backends.smtp.EmailBackend" if os.environ.get("EMAIL_HOST")
                 else "django.core.mail.backends.console.EmailBackend")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", 587))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "smti-hub@localhost")

# --- Security --------------------------------------------------------------
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_COOKIE_AGE = 60 * 60 * 8

if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", True) and not TESTING
    # The healthcheck runs inside the container over the loopback, where there
    # is no TLS to redirect to. Everything else still gets redirected.
    SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h not in {"localhost", "127.0.0.1"}]

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"plain": {"format": "{asctime} {levelname} {name} {message}", "style": "{"}},
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "plain"},
        "mail_admins": {"class": "django.utils.log.AdminEmailHandler", "level": "ERROR",
                        "include_html": False},
    },
    "root": {"handlers": ["console"], "level": os.environ.get("DJANGO_LOG_LEVEL", "INFO")},
    "loggers": {
        # A 500 that only ever reaches docker logs is a 500 nobody hears about.
        "django.request": {"handlers": ["console", "mail_admins"], "level": "ERROR",
                           "propagate": False},
        # Bind failures and group-search problems are the whole diagnosis when
        # AD auth breaks; at INFO they are invisible.
        "django_auth_ldap": {"handlers": ["console"],
                             "level": os.environ.get("LDAP_LOG_LEVEL", "WARNING"),
                             "propagate": False},
    },
}
