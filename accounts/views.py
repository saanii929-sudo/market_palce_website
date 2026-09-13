from django.contrib.auth import authenticate, get_user_model
from django.db import transaction
from rest_framework import generics, parsers, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from core.defaults import CannotDeleteOnlyDefaultError, handle_deletion
from core.throttling import OTPBurstThrottle, OTPSustainedThrottle

from .models import Address, OTPCode
from .serializers import (
    AddressSerializer,
    AvatarUploadSerializer,
    LoginSerializer,
    LogoutSerializer,
    OTPSendSerializer,
    OTPVerifySerializer,
    PasswordForgotSerializer,
    PasswordResetSerializer,
    RegisterSerializer,
    SocialAuthSerializer,
    UserInterestUpdateSerializer,
    UserSerializer,
)
from .services import social as social_service
from .services.otp import OTPVerificationError, send_otp, verify_otp

User = get_user_model()


def tokens_for_user(user):
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with transaction.atomic():
            user = serializer.save()
            destination = user.email or user.phone
            send_otp(destination, OTPCode.Purpose.SIGNUP_VERIFY, user=user)

        return Response(
            {
                "detail": "Account created. Please verify your account with the code we sent.",
                "user": UserSerializer(user).data,
            },
            status=status.HTTP_201_CREATED,
        )


class OTPSendView(APIView):
    serializer_class = OTPSendSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [OTPBurstThrottle, OTPSustainedThrottle]

    def post(self, request):
        serializer = OTPSendSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        destination = serializer.validated_data["destination"]
        purpose = serializer.validated_data["purpose"]

        user = User.objects.filter(email__iexact=destination).first() or User.objects.filter(
            phone=destination
        ).first()

        if purpose == OTPCode.Purpose.PASSWORD_RESET and user is None:
            # Don't reveal whether an account exists.
            return Response({"detail": "If an account exists, a code has been sent."})

        send_otp(destination, purpose, user=user)
        return Response({"detail": "Verification code sent."})


class OTPVerifyView(APIView):
    serializer_class = OTPVerifySerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = OTPVerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        destination = serializer.validated_data["destination"]
        purpose = serializer.validated_data["purpose"]
        code = serializer.validated_data["code"]

        consume = purpose == OTPCode.Purpose.SIGNUP_VERIFY
        try:
            otp = verify_otp(destination, purpose, code, consume=consume)
        except OTPVerificationError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=status.HTTP_400_BAD_REQUEST)

        if purpose == OTPCode.Purpose.SIGNUP_VERIFY:
            user = otp.user
            if user is None:
                return Response(
                    {"detail": "No account associated with this code.", "code": "no_user"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            channel_field = "is_email_verified" if otp.channel == OTPCode.Channel.EMAIL else "is_phone_verified"
            setattr(user, channel_field, True)
            user.save(update_fields=[channel_field])
            return Response({"detail": "Account verified.", "user": UserSerializer(user).data, **tokens_for_user(user)})

        return Response({"detail": "Code verified."})


class LoginView(APIView):
    serializer_class = LoginSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [UserRateThrottle]

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        identifier = serializer.validated_data["identifier"]
        password = serializer.validated_data["password"]

        user = authenticate(request, username=identifier, password=password)
        if user is None:
            return Response(
                {"detail": "Invalid credentials.", "code": "invalid_credentials"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if not user.is_verified:
            return Response(
                {"detail": "Please verify your account before logging in.", "code": "unverified_account"},
                status=status.HTTP_403_FORBIDDEN,
            )

        if request.session.session_key:
            from cart.services import merge_guest_cart_into_user_cart
            from wishlist.services import merge_guest_wishlist_into_user_wishlist

            merge_guest_cart_into_user_cart(request.session.session_key, user)
            merge_guest_wishlist_into_user_wishlist(request.session.session_key, user)

        return Response({"user": UserSerializer(user).data, **tokens_for_user(user)})


class LogoutView(APIView):
    serializer_class = LogoutSerializer
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            token = RefreshToken(serializer.validated_data["refresh"])
            token.blacklist()
        except TokenError:
            return Response({"detail": "Invalid or already-expired refresh token."}, status=status.HTTP_400_BAD_REQUEST)
        return Response(status=status.HTTP_204_NO_CONTENT)


class BaseSocialAuthView(APIView):
    serializer_class = SocialAuthSerializer
    permission_classes = [permissions.AllowAny]
    provider_id_field = None

    def verify(self, token: str) -> dict:
        raise NotImplementedError

    def post(self, request):
        serializer = SocialAuthSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            profile = self.verify(serializer.validated_data["token"])
        except social_service.SocialAuthError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)

        email = profile.get("email")
        if not email:
            return Response({"detail": "Provider did not return an email address."}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(email__iexact=email).first()
        created = False
        if user is None:
            user = User(
                email=email,
                full_name=profile.get("full_name", ""),
                is_email_verified=True,
            )
            user.set_unusable_password()
            user.save()
            created = True
        elif not user.is_email_verified:
            user.is_email_verified = True
            user.save(update_fields=["is_email_verified"])

        return Response(
            {"user": UserSerializer(user).data, **tokens_for_user(user)},
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )


class GoogleAuthView(BaseSocialAuthView):
    def verify(self, token: str) -> dict:
        return social_service.verify_google_token(token)


class AppleAuthView(BaseSocialAuthView):
    def verify(self, token: str) -> dict:
        return social_service.verify_apple_token(token)


class PasswordForgotView(APIView):
    serializer_class = PasswordForgotSerializer
    permission_classes = [permissions.AllowAny]
    throttle_classes = [OTPBurstThrottle, OTPSustainedThrottle]

    def post(self, request):
        serializer = PasswordForgotSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        destination = serializer.validated_data["destination"]

        user = User.objects.filter(email__iexact=destination).first() or User.objects.filter(
            phone=destination
        ).first()

        if user is not None:
            send_otp(destination, OTPCode.Purpose.PASSWORD_RESET, user=user)

        # Always return the same response so account existence isn't leaked.
        return Response({"detail": "If an account exists, a reset code has been sent."})


class PasswordResetView(APIView):
    serializer_class = PasswordResetSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = PasswordResetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        destination = serializer.validated_data["destination"]
        code = serializer.validated_data["code"]
        new_password = serializer.validated_data["new_password"]

        try:
            otp = verify_otp(destination, OTPCode.Purpose.PASSWORD_RESET, code, consume=True)
        except OTPVerificationError as exc:
            return Response({"detail": exc.message, "code": exc.code}, status=status.HTTP_400_BAD_REQUEST)

        user = otp.user
        if user is None:
            return Response({"detail": "No account associated with this code."}, status=status.HTTP_400_BAD_REQUEST)

        user.set_password(new_password)
        user.save(update_fields=["password"])

        # Invalidate other sessions by blacklisting all outstanding refresh tokens.
        from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken

        for outstanding in OutstandingToken.objects.filter(user=user):
            BlacklistedToken.objects.get_or_create(token=outstanding)

        return Response({"detail": "Password reset successfully. Please log in again."})


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user


class InterestsUpdateView(APIView):
    serializer_class = UserInterestUpdateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request):
        serializer = UserInterestUpdateSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        return Response(UserSerializer(user).data)


class AvatarUploadView(APIView):
    serializer_class = AvatarUploadSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [parsers.MultiPartParser, parsers.FormParser]

    def post(self, request):
        serializer = AvatarUploadSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(UserSerializer(request.user).data)


class AddressViewSet(viewsets.ModelViewSet):
    serializer_class = AddressSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Address.objects.none()
        return Address.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def destroy(self, request, *args, **kwargs):
        address = self.get_object()
        try:
            handle_deletion(address)
        except CannotDeleteOnlyDefaultError as exc:
            return Response({"detail": exc.message}, status=status.HTTP_400_BAD_REQUEST)
        return super().destroy(request, *args, **kwargs)

    @action(detail=True, methods=["post"])
    def set_default(self, request, pk=None):
        address = self.get_object()
        address.is_default = True
        address.save(update_fields=["is_default"])
        return Response(AddressSerializer(address).data)
