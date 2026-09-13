from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("payments/methods", views.PaymentMethodViewSet, basename="payment-method")

urlpatterns = router.urls
