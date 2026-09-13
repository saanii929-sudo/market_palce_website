import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.tests.factories import UserFactory
from orders.models import Order
from orders.tests.factories import OrderItemFactory

from ..models import Review
from .factories import ReviewFactory


def authed_client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
def test_create_review_for_delivered_order_item():
    order_item = OrderItemFactory()  # OrderFactory defaults to DELIVERED
    client = authed_client(order_item.order.user)

    response = client.post(reverse("review-create"), {"order_item_id": order_item.id, "rating": 5, "comment": "Loved it"})
    assert response.status_code == 201
    assert Review.objects.filter(order_item=order_item, rating=5).exists()

    order_item.product.refresh_from_db()
    assert order_item.product.review_count == 1
    assert float(order_item.product.avg_rating) == 5.0


@pytest.mark.django_db
def test_cannot_review_order_item_belonging_to_someone_else():
    order_item = OrderItemFactory()
    other_user = UserFactory(email="notmine@example.com")
    client = authed_client(other_user)

    response = client.post(reverse("review-create"), {"order_item_id": order_item.id, "rating": 4})
    assert response.status_code == 400


@pytest.mark.django_db
def test_cannot_review_undelivered_order():
    order_item = OrderItemFactory(order__status=Order.Status.PROCESSING)
    client = authed_client(order_item.order.user)

    response = client.post(reverse("review-create"), {"order_item_id": order_item.id, "rating": 4})
    assert response.status_code == 400


@pytest.mark.django_db
def test_cannot_review_same_order_item_twice():
    order_item = OrderItemFactory()
    client = authed_client(order_item.order.user)

    first = client.post(reverse("review-create"), {"order_item_id": order_item.id, "rating": 4})
    assert first.status_code == 201

    second = client.post(reverse("review-create"), {"order_item_id": order_item.id, "rating": 2})
    assert second.status_code == 400


@pytest.mark.django_db
def test_buying_same_product_twice_allows_two_reviews():
    from catalog.tests.factories import ProductFactory
    from orders.tests.factories import OrderFactory

    user = UserFactory(email="repeatbuyer@example.com")
    product = ProductFactory()
    order_a = OrderFactory(user=user)
    order_b = OrderFactory(user=user)
    item_a = OrderItemFactory(order=order_a, product=product)
    item_b = OrderItemFactory(order=order_b, product=product)

    client = authed_client(user)
    r1 = client.post(reverse("review-create"), {"order_item_id": item_a.id, "rating": 5})
    r2 = client.post(reverse("review-create"), {"order_item_id": item_b.id, "rating": 3})

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert Review.objects.filter(product=product).count() == 2


@pytest.mark.django_db
def test_owner_can_update_review():
    review = ReviewFactory(rating=3, comment="Meh")
    client = authed_client(review.user)

    response = client.patch(reverse("review-detail", kwargs={"pk": review.id}), {"rating": 5, "comment": "Actually great"})
    assert response.status_code == 200
    assert response.data["rating"] == 5

    review.product.refresh_from_db()
    assert float(review.product.avg_rating) == 5.0


@pytest.mark.django_db
def test_non_owner_cannot_update_review():
    review = ReviewFactory()
    other = UserFactory(email="intruder@example.com")
    client = authed_client(other)

    response = client.patch(reverse("review-detail", kwargs={"pk": review.id}), {"rating": 1})
    assert response.status_code == 403


@pytest.mark.django_db
def test_owner_can_delete_review_and_rating_recomputes():
    review = ReviewFactory(rating=5)
    product = review.product
    client = authed_client(review.user)

    response = client.delete(reverse("review-detail", kwargs={"pk": review.id}))
    assert response.status_code == 204

    product.refresh_from_db()
    assert product.review_count == 0
    assert float(product.avg_rating) == 0.0


@pytest.mark.django_db
def test_non_owner_cannot_delete_review():
    review = ReviewFactory()
    other = UserFactory(email="intruder2@example.com")
    client = authed_client(other)

    response = client.delete(reverse("review-detail", kwargs={"pk": review.id}))
    assert response.status_code == 403
    assert Review.objects.filter(id=review.id).exists()


@pytest.mark.django_db
def test_review_create_requires_authentication():
    order_item = OrderItemFactory()
    client = APIClient()
    response = client.post(reverse("review-create"), {"order_item_id": order_item.id, "rating": 4})
    assert response.status_code == 401
