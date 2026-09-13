from rest_framework.throttling import SimpleRateThrottle


class BaseOTPThrottle(SimpleRateThrottle):
    """Rate-limits OTP send requests per destination (email/phone), not per user,
    since OTP requests can happen pre-signup."""

    def get_cache_key(self, request, view):
        destination = request.data.get("destination", "")
        if not destination:
            return None
        return self.cache_format % {
            "scope": self.scope,
            "ident": destination.strip().lower(),
        }


class OTPBurstThrottle(BaseOTPThrottle):
    """Approximates the UI's "resend in 30s" cooldown."""

    scope = "otp_burst"
    rate = "2/min"


class OTPSustainedThrottle(BaseOTPThrottle):
    """Caps total OTP requests per destination per hour to prevent SMS-bombing."""

    scope = "otp_sustained"
    rate = "5/hour"
