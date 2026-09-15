"""
Django settings for sports_shop project.
"""
from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    CELERY_TASK_ALWAYS_EAGER=(bool, True),
)
env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))

SECRET_KEY = env("SECRET_KEY", default="django-insecure-kr$l8a-a2yj_4lwf&c@rnfg))3rt44q2src+u!#%-#m2nulw64")
DEBUG = env("DEBUG")
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

# Coolify (and most PaaS setups) terminate TLS at a reverse proxy and forward
# plain HTTP to this container, so Django needs to be told which header to
# trust for "is this request actually HTTPS" - without this, request.is_secure()
# is always False behind the proxy, which breaks secure cookies and the
# require_https redirect-safety checks used throughout the web app.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = env.list("CSRF_TRUSTED_ORIGINS", default=[])
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third-party
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_spectacular",
    "django_filters",
    "storages",
    # local
    "core",
    "catalog",
    "accounts",
    "discovery",
    "cart",
    "wishlist",
    "orders",
    "payments",
    "sellers",
    "pos",
    "reviews",
    "notifications",
    "support",
    "cms",
    "web",
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
]

ROOT_URLCONF = "sports_shop.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "web.context_processors.site_chrome",
            ],
        },
    },
]

WSGI_APPLICATION = "sports_shop.wsgi.application"


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASES = {
    "default": env.db("DATABASE_URL", default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}"),
}


# Custom user model

AUTH_USER_MODEL = "accounts.User"

AUTHENTICATION_BACKENDS = [
    "accounts.backends.EmailOrPhoneBackend",
    "django.contrib.auth.backends.ModelBackend",
]


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True


# Static & media files

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Static files are always served by whitenoise (works the same whether media
# ends up on local disk or S3 below) so no separate nginx/CDN is required to
# just "keep the project running" in a container.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# S3-compatible media storage (only enabled when a bucket name is provided -
# requires the optional `django-storages` package). Without it, uploaded
# media (product photos, seller documents, avatars) lives on the "media"
# volume defined in docker-compose.yml instead. Accepts either Cloudflare
# R2's R2_* names or the generic AWS_* names, so either convention in .env
# works.
AWS_STORAGE_BUCKET_NAME = env("R2_BUCKET_NAME", default=env("AWS_STORAGE_BUCKET_NAME", default=""))
if AWS_STORAGE_BUCKET_NAME:
    AWS_ACCESS_KEY_ID = env("R2_ACCESS_KEY_ID", default=env("AWS_ACCESS_KEY_ID", default=""))
    AWS_SECRET_ACCESS_KEY = env("R2_SECRET_ACCESS_KEY", default=env("AWS_SECRET_ACCESS_KEY", default=""))
    AWS_S3_ENDPOINT_URL = env("R2_ENDPOINT", default=env("AWS_S3_ENDPOINT_URL", default=None))
    AWS_S3_REGION_NAME = env("R2_REGION", default=env("AWS_S3_REGION_NAME", default="auto"))
    AWS_DEFAULT_ACL = None
    AWS_S3_FILE_OVERWRITE = False
    # R2 requires SigV4 and path-style addressing, since the account-level
    # endpoint has no per-bucket subdomain -
    # https://developers.cloudflare.com/r2/examples/aws/boto3/
    AWS_S3_SIGNATURE_VERSION = "s3v4"
    AWS_S3_ADDRESSING_STYLE = "path"
    # R2's public bucket domain (R2_PUBLIC_URL), if set, makes uploaded
    # files' .url resolve to a direct public link instead of a signed one.
    _r2_public_url = env("R2_PUBLIC_URL", default="")
    if _r2_public_url:
        AWS_S3_CUSTOM_DOMAIN = _r2_public_url.removeprefix("https://").removeprefix("http://")
        AWS_QUERYSTRING_AUTH = False
    STORAGES["default"] = {"BACKEND": "storages.backends.s3.S3Storage"}


# Django REST Framework

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_FILTER_BACKENDS": ("django_filters.rest_framework.DjangoFilterBackend",),
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "core.exceptions.exception_handler",
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {
        "user": "60/min",
        "anon": "30/min",
        "otp_burst": "2/min",
        "otp_sustained": "5/hour",
    },
}

SPECTACULAR_SETTINGS = {
    "TITLE": "SportTech Backend API",
    "DESCRIPTION": "API for the SportTech e-commerce mobile app.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "ENUM_NAME_OVERRIDES": {
        "OrderStatusEnum": "orders.models.Order.Status",
        "PaymentStatusEnum": "orders.models.Payment.Status",
        "OrderPaymentGatewayEnum": "orders.models.Payment.Gateway",
        "SavedPaymentGatewayEnum": "payments.models.PaymentMethodToken.Gateway",
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("ACCESS_TOKEN_LIFETIME_MINUTES", default=30)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("REFRESH_TOKEN_LIFETIME_DAYS", default=14)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}


# Cache (used for OTP throttling; swap to Redis in production)

REDIS_URL = env("REDIS_URL", default=None)
if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
    }
else:
    CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


# Celery

CELERY_BROKER_URL = REDIS_URL or "memory://"
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ALWAYS_EAGER = env("CELERY_TASK_ALWAYS_EAGER")
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE

CELERY_BEAT_SCHEDULE = {
    "deactivate-expired-flash-deals": {
        "task": "catalog.tasks.deactivate_expired_flash_deals",
        "schedule": 60.0,
    },
}


# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration
#
# Django 6.1's MAILERS setting is the current (non-deprecated) way to
# configure a named mailer - individual top-level EMAIL_* settings still
# work but are deprecated as of 6.1 and will be removed in 7.0.

_email_port = env.int("SMTP_PORT", default=env.int("EMAIL_PORT", default=587))
# Port 465 is implicit SSL; everything else (587, 25, ...) uses STARTTLS -
# Django's SMTP backend errors if both use_tls and use_ssl are true.
_email_use_ssl = _email_port == 465

MAILERS = {
    "default": {
        "BACKEND": env("EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"),
        "OPTIONS": {
            "host": env("SMTP_HOST", default=env("EMAIL_HOST", default="")),
            "port": _email_port,
            "username": env("SMTP_USERNAME", default=env("EMAIL_HOST_USER", default="")),
            "password": env("SMTP_PASSWORD", default=env("EMAIL_HOST_PASSWORD", default="")),
            "use_tls": not _email_use_ssl,
            "use_ssl": _email_use_ssl,
        },
    },
}
DEFAULT_FROM_EMAIL = env("SMTP_FROM", default=env("DEFAULT_FROM_EMAIL", default="no-reply@sportshop.example"))


# SMS (see core/sms.py for the pluggable backend interface)

SMS_BACKEND = env("SMS_BACKEND", default="core.sms.ConsoleSMSBackend")
AFRICASTALKING_USERNAME = env("AFRICASTALKING_USERNAME", default="")
AFRICASTALKING_API_KEY = env("AFRICASTALKING_API_KEY", default="")
MNOTIFY_API_KEY = env("MNOTIFY_API_KEY", default="")
MNOTIFY_SENDER_ID = env("MNOTIFY_SENDER_ID", default="SportShop")


# Social auth

GOOGLE_OAUTH_CLIENT_ID = env("GOOGLE_OAUTH_CLIENT_ID", default="")
APPLE_CLIENT_ID = env("APPLE_CLIENT_ID", default="")


# Payments (see orders/services/payment_gateway.py for the pluggable backend
# interface - PAYMENT_GATEWAY_BACKENDS defaults to Mock everywhere until real
# credentials are added here)

PAYMENT_DEFAULT_GATEWAY = env("PAYMENT_DEFAULT_GATEWAY", default="paystack")
PAYSTACK_SECRET_KEY = env("PAYSTACK_SECRET_KEY", default="")
FLUTTERWAVE_SECRET_HASH = env("FLUTTERWAVE_SECRET_HASH", default="")

# Hubtel (hosted checkout - see orders/services/payment_gateway.py::HubtelGateway
# and orders/services/hubtel_checkout.py for the deferred order-on-payment-success flow)
HUBTEL_API_ID = env("HUBTEL_API_ID", default="")
HUBTEL_API_KEY = env("HUBTEL_API_KEY", default="")
HUBTEL_MERCHANT_ACCOUNT = env("HUBTEL_MERCHANT_ACCOUNT", default="")
# Fallback redirect targets if a client doesn't supply its own return_url/
# cancellation_url when starting checkout. The callback (webhook) URL is
# always built server-side from the request, not read from here, since it
# must point at this backend regardless of what a client requests.
HUBTEL_RETURN_URL = env("HUBTEL_RETURN_URL", default="")
HUBTEL_CANCELLATION_URL = env("HUBTEL_CANCELLATION_URL", default="")
