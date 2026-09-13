from django.core.mail import send_mail
from django.conf import settings

from core.sms import get_sms_backend

from ..models import OTPCode


def determine_channel(destination: str) -> str:
    return OTPCode.Channel.EMAIL if "@" in destination else OTPCode.Channel.SMS


def send_otp(destination: str, purpose: str, user=None) -> OTPCode:
    channel = determine_channel(destination)
    otp, raw_code = OTPCode.issue(destination=destination, channel=channel, purpose=purpose, user=user)
    deliver_otp(destination, channel, raw_code, purpose)
    return otp


def deliver_otp(destination: str, channel: str, raw_code: str, purpose: str) -> None:
    if purpose == OTPCode.Purpose.PASSWORD_RESET:
        message = f"Your password reset code is {raw_code}. It expires in {OTPCode.VALIDITY_MINUTES} minutes."
    else:
        message = f"Your verification code is {raw_code}. It expires in {OTPCode.VALIDITY_MINUTES} minutes."

    if channel == OTPCode.Channel.EMAIL:
        send_mail(
            subject="Your verification code",
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[destination],
            using="default",
        )
    else:
        get_sms_backend().send(destination, message)


def get_active_otp(destination: str, purpose: str):
    return (
        OTPCode.objects.filter(destination=destination, purpose=purpose, is_used=False)
        .order_by("-created_at")
        .first()
    )


class OTPVerificationError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def verify_otp(destination: str, purpose: str, raw_code: str, consume: bool = True) -> OTPCode:
    """Validates a code against the latest unused OTP for (destination, purpose).

    `consume=False` lets a caller check validity without spending the code -
    used by the password-reset flow, where the code is checked once at the
    "enter code" step and again (consumed) at the "set new password" step.
    """
    otp = get_active_otp(destination, purpose)
    if otp is None:
        raise OTPVerificationError("not_found", "No active verification code found. Please request a new one.")
    if otp.is_expired:
        raise OTPVerificationError("expired", "This code has expired. Please request a new one.")
    if otp.is_locked:
        raise OTPVerificationError("locked", "Too many incorrect attempts. Please request a new code.")

    if not otp.check_code(raw_code):
        raise OTPVerificationError("invalid", "The code you entered is incorrect.")

    if consume:
        otp.mark_used()
    return otp
