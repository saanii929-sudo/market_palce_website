from django.urls import include, path
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

router = DefaultRouter()
router.register("accounts/addresses", views.AddressViewSet, basename="address")

urlpatterns = [
    path("auth/register/", views.RegisterView.as_view(), name="register"),
    path("auth/otp/send/", views.OTPSendView.as_view(), name="otp-send"),
    path("auth/otp/verify/", views.OTPVerifyView.as_view(), name="otp-verify"),
    path("auth/login/", views.LoginView.as_view(), name="login"),
    path("auth/token/refresh/", TokenRefreshView.as_view(), name="token-refresh"),
    path("auth/logout/", views.LogoutView.as_view(), name="logout"),
    path("auth/social/google/", views.GoogleAuthView.as_view(), name="social-google"),
    path("auth/social/firebase/", views.FirebaseAuthView.as_view(), name="social-firebase"),
    path("auth/social/apple/", views.AppleAuthView.as_view(), name="social-apple"),
    path("auth/password/forgot/", views.PasswordForgotView.as_view(), name="password-forgot"),
    path("auth/password/reset/", views.PasswordResetView.as_view(), name="password-reset"),
    path("accounts/me/", views.MeView.as_view(), name="me"),
    path("accounts/me/interests/", views.InterestsUpdateView.as_view(), name="me-interests"),
    path("accounts/me/avatar/", views.AvatarUploadView.as_view(), name="me-avatar"),
    path("", include(router.urls)),
]
