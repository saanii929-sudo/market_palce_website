from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from accounts.models import User
from catalog.models import Seller

from .models import SellerApplication
from .notifications import notify_application_reviewed


class SellerApplicationError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def submit_application(
    user, *, business_name: str, category, phone: str, id_document=None, business_certificate=None
) -> SellerApplication:
    if SellerApplication.objects.filter(user=user, status=SellerApplication.Status.PENDING).exists():
        raise SellerApplicationError("You already have a pending seller application.")
    if user.role == User.Role.SELLER:
        raise SellerApplicationError("You're already a seller.")
    if id_document is None:
        raise SellerApplicationError("Please upload a government-issued ID to apply.")

    return SellerApplication.objects.create(
        user=user, business_name=business_name, category=category, phone=phone,
        id_document=id_document, business_certificate=business_certificate,
    )


def _unique_seller_slug(business_name: str) -> str:
    base = slugify(business_name) or "seller"
    slug = base
    suffix = 1
    while Seller.objects.filter(slug=slug).exists():
        suffix += 1
        slug = f"{base}-{suffix}"
    return slug


@transaction.atomic
def approve_application(application: SellerApplication, reviewer_note: str = "") -> Seller:
    if application.status != SellerApplication.Status.PENDING:
        raise SellerApplicationError("Only pending applications can be approved.")

    application.status = SellerApplication.Status.APPROVED
    application.reviewed_at = timezone.now()
    application.reviewer_note = reviewer_note
    application.save(update_fields=["status", "reviewed_at", "reviewer_note"])

    application.user.role = User.Role.SELLER
    application.user.save(update_fields=["role"])

    seller = Seller.objects.create(
        user=application.user,
        business_name=application.business_name,
        slug=_unique_seller_slug(application.business_name),
    )

    notify_application_reviewed(application)
    return seller


def reject_application(application: SellerApplication, reviewer_note: str = "") -> SellerApplication:
    if application.status != SellerApplication.Status.PENDING:
        raise SellerApplicationError("Only pending applications can be rejected.")

    application.status = SellerApplication.Status.REJECTED
    application.reviewed_at = timezone.now()
    application.reviewer_note = reviewer_note
    application.save(update_fields=["status", "reviewed_at", "reviewer_note"])

    notify_application_reviewed(application)
    return application
