from django.conf import settings
from django.conf.urls.static import static
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("api/v1/", include("accounts.urls")),
    path("api/v1/", include("catalog.urls")),
    path("api/v1/", include("discovery.urls")),
    path("api/v1/", include("cart.urls")),
    path("api/v1/", include("wishlist.urls")),
    path("api/v1/", include("orders.urls")),
    path("api/v1/", include("payments.urls")),
    path("api/v1/", include("sellers.urls")),
    path("api/v1/", include("reviews.urls")),
    path("api/v1/", include("notifications.urls")),
    path("api/v1/", include("support.urls")),
    path("api/v1/", include("cms.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("", include("web.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
