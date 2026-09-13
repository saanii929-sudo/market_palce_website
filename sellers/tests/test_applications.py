import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.models import User
from accounts.tests.factories import UserFactory
from catalog.models import Seller
from catalog.tests.factories import CategoryFactory

from ..models import SellerApplication


def authed_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


def fake_id_document():
    return SimpleUploadedFile("id.jpg", b"fake-id-bytes", content_type="image/jpeg")


@pytest.mark.django_db
def test_submit_application():
    user = UserFactory(email="applicant@example.com")
    category = CategoryFactory()
    client = authed_client(user)

    response = client.post(
        reverse("seller-apply"),
        {
            "business_name": "Jane's Sportswear", "category_id": category.id, "phone": "+233201234567",
            "id_document": fake_id_document(),
        },
        format="multipart",
    )
    assert response.status_code == 201
    assert response.data["status"] == SellerApplication.Status.PENDING


@pytest.mark.django_db
def test_cannot_submit_second_pending_application():
    user = UserFactory(email="doubleapply@example.com")
    category = CategoryFactory()
    client = authed_client(user)

    client.post(
        reverse("seller-apply"),
        {"business_name": "First", "category_id": category.id, "phone": "+233201234567", "id_document": fake_id_document()},
        format="multipart",
    )
    response = client.post(
        reverse("seller-apply"),
        {"business_name": "Second", "category_id": category.id, "phone": "+233201234567", "id_document": fake_id_document()},
        format="multipart",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_application_status_endpoint_returns_latest():
    user = UserFactory(email="statuscheck@example.com")
    category = CategoryFactory()
    client = authed_client(user)
    client.post(
        reverse("seller-apply"),
        {"business_name": "Biz", "category_id": category.id, "phone": "+233201234567", "id_document": fake_id_document()},
        format="multipart",
    )

    response = client.get(reverse("seller-apply-status"))
    assert response.status_code == 200
    assert response.data["business_name"] == "Biz"


@pytest.mark.django_db
def test_application_status_404_when_none_submitted():
    user = UserFactory(email="nostatus@example.com")
    client = authed_client(user)
    response = client.get(reverse("seller-apply-status"))
    assert response.status_code == 404


@pytest.mark.django_db
def test_admin_can_approve_application_and_it_creates_seller_and_flips_role():
    applicant = UserFactory(email="soontobeseller@example.com")
    admin_user = UserFactory(email="admin@example.com", is_staff=True, is_superuser=True)
    category = CategoryFactory()
    client = authed_client(applicant)
    apply_response = client.post(
        reverse("seller-apply"),
        {
            "business_name": "Kicks & Co", "category_id": category.id, "phone": "+233201234567",
            "id_document": fake_id_document(),
        },
        format="multipart",
    )
    application_id = apply_response.data["id"]

    admin_client = authed_client(admin_user)
    response = admin_client.post(
        reverse("admin-seller-application-review", kwargs={"pk": application_id}),
        {"action": "approve", "reviewer_note": "Looks good"},
    )
    assert response.status_code == 200
    assert response.data["status"] == SellerApplication.Status.APPROVED

    applicant.refresh_from_db()
    assert applicant.role == User.Role.SELLER
    assert Seller.objects.filter(user=applicant, business_name="Kicks & Co").exists()


@pytest.mark.django_db
def test_admin_can_reject_application():
    applicant = UserFactory(email="rejectme@example.com")
    admin_user = UserFactory(email="admin2@example.com", is_staff=True, is_superuser=True)
    category = CategoryFactory()
    client = authed_client(applicant)
    apply_response = client.post(
        reverse("seller-apply"),
        {"business_name": "Biz", "category_id": category.id, "phone": "+233201234567", "id_document": fake_id_document()},
        format="multipart",
    )

    admin_client = authed_client(admin_user)
    response = admin_client.post(
        reverse("admin-seller-application-review", kwargs={"pk": apply_response.data["id"]}),
        {"action": "reject", "reviewer_note": "Incomplete info"},
    )
    assert response.status_code == 200
    assert response.data["status"] == SellerApplication.Status.REJECTED

    applicant.refresh_from_db()
    assert applicant.role == User.Role.CUSTOMER


@pytest.mark.django_db
def test_non_staff_cannot_access_admin_endpoints():
    user = UserFactory(email="notstaff@example.com")
    client = authed_client(user)
    response = client.get(reverse("admin-seller-application-list"))
    assert response.status_code == 403


@pytest.mark.django_db
def test_admin_list_filters_by_status():
    admin_user = UserFactory(email="admin3@example.com", is_staff=True, is_superuser=True)
    category = CategoryFactory()
    applicant = UserFactory(email="filterme@example.com")
    authed_client(applicant).post(
        reverse("seller-apply"),
        {"business_name": "Biz", "category_id": category.id, "phone": "+233201234567", "id_document": fake_id_document()},
        format="multipart",
    )

    admin_client = authed_client(admin_user)
    pending = admin_client.get(reverse("admin-seller-application-list"), {"status": "pending"})
    approved = admin_client.get(reverse("admin-seller-application-list"), {"status": "approved"})
    assert pending.data["count"] == 1
    assert approved.data["count"] == 0
