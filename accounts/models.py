import hashlib
import secrets

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from catalog.models import Category
from core.models import TimeStampedModel

from .managers import UserManager

phone_validator = RegexValidator(
    regex=r"^\+?[1-9]\d{6,14}$",
    message="Enter a valid phone number in international format, e.g. +233201234567.",
)


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    class Role(models.TextChoices):
        CUSTOMER = "customer", "Customer"
        SELLER = "seller", "Seller"
        RIDER = "rider", "Rider"
        ADMIN = "admin", "Admin"

    email = models.EmailField(unique=True, null=True, blank=True)
    phone = models.CharField(
        max_length=20,
        unique=True,
        null=True,
        blank=True,
        validators=[phone_validator],
    )

    full_name = models.CharField(max_length=150, blank=True)
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)
    bio = models.TextField(blank=True)

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.CUSTOMER)

    is_email_verified = models.BooleanField(default=False)
    is_phone_verified = models.BooleanField(default=False)

    push_notifications_enabled = models.BooleanField(default=True)
    email_offers_enabled = models.BooleanField(default=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    interests = models.ManyToManyField(
        Category, through="UserInterest", related_name="interested_users", blank=True
    )

    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []
    EMAIL_FIELD = "email"

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(email__isnull=False) | models.Q(phone__isnull=False),
                name="user_must_have_email_or_phone",
            )
        ]

    def __str__(self):
        return self.email or self.phone or str(self.pk)

    @property
    def is_verified(self):
        return self.is_email_verified or self.is_phone_verified

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else (self.email or self.phone)


class UserInterest(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="user_interests")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="user_interests")

    class Meta:
        unique_together = ("user", "category")

    def __str__(self):
        return f"{self.user} -> {self.category}"


class Address(TimeStampedModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(max_length=50, blank=True, help_text="e.g. Home, Office")
    recipient_name = models.CharField(max_length=150)
    phone = models.CharField(max_length=20, validators=[phone_validator])
    line1 = models.CharField(max_length=255)
    line2 = models.CharField(max_length=255, blank=True)
    city = models.CharField(max_length=100)
    region = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, default="Ghana")
    is_default = models.BooleanField(default=False)

    class Meta:
        ordering = ["-is_default", "-created_at"]

    def __str__(self):
        return f"{self.recipient_name} - {self.line1}, {self.city}"

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        super().save(*args, **kwargs)
        if self.is_default:
            Address.objects.filter(user=self.user).exclude(id=self.id).update(is_default=False)
        elif is_new:
            from core.defaults import assign_default_on_create

            assign_default_on_create(self)


def generate_otp_code():
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp_code(raw_code: str) -> str:
    return hashlib.sha256(raw_code.encode()).hexdigest()


class OTPCode(TimeStampedModel):
    class Purpose(models.TextChoices):
        SIGNUP_VERIFY = "signup_verify", "Signup Verification"
        PASSWORD_RESET = "password_reset", "Password Reset"

    class Channel(models.TextChoices):
        EMAIL = "email", "Email"
        SMS = "sms", "SMS"

    user = models.ForeignKey(
        User, on_delete=models.CASCADE, null=True, blank=True, related_name="otp_codes"
    )
    destination = models.CharField(max_length=255)
    channel = models.CharField(max_length=10, choices=Channel.choices)
    purpose = models.CharField(max_length=20, choices=Purpose.choices)

    code_hash = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    is_used = models.BooleanField(default=False)

    MAX_ATTEMPTS = 5
    VALIDITY_MINUTES = 10

    class Meta:
        indexes = [
            models.Index(fields=["destination", "purpose", "is_used"]),
        ]

    def __str__(self):
        return f"OTP({self.destination}, {self.purpose})"

    @classmethod
    def issue(cls, destination: str, channel: str, purpose: str, user=None) -> tuple["OTPCode", str]:
        raw_code = generate_otp_code()
        otp = cls.objects.create(
            user=user,
            destination=destination,
            channel=channel,
            purpose=purpose,
            code_hash=hash_otp_code(raw_code),
            expires_at=timezone.now() + timezone.timedelta(minutes=cls.VALIDITY_MINUTES),
        )
        return otp, raw_code

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def is_locked(self) -> bool:
        return self.attempts >= self.MAX_ATTEMPTS

    def check_code(self, raw_code: str) -> bool:
        if self.is_used or self.is_expired or self.is_locked:
            return False
        self.attempts = models.F("attempts") + 1
        self.save(update_fields=["attempts"])
        self.refresh_from_db(fields=["attempts"])
        return hash_otp_code(raw_code) == self.code_hash

    def mark_used(self):
        self.is_used = True
        self.save(update_fields=["is_used"])
