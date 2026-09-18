import pytest
from django.test import Client
from django.urls import reverse

from accounts.tests.factories import UserFactory
from catalog.tests.factories import ProductFactory
from orders.models import Order
from orders.tests.factories import OrderItemFactory
from reviews.models import Review


def _client_for(user):
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
def test_customer_with_delivered_item_sees_review_form():
    user = UserFactory()
    product = ProductFactory()
    OrderItemFactory(product=product, order__user=user, order__status=Order.Status.DELIVERED)

    client = _client_for(user)
    response = client.get(reverse("web-product-detail", args=[product.slug]))
    assert response.status_code == 200
    assert b"Rate your purchase" in response.content


@pytest.mark.django_db
def test_customer_can_submit_a_review():
    user = UserFactory()
    product = ProductFactory()
    order_item = OrderItemFactory(product=product, order__user=user, order__status=Order.Status.DELIVERED)

    client = _client_for(user)
    response = client.post(reverse("web-product-review-add", args=[product.slug]), {
        "order_item_id": order_item.id, "rating": "5", "comment": "Great fit!",
    })
    assert response.status_code == 302
    review = Review.objects.get(order_item=order_item)
    assert review.rating == 5
    assert review.comment == "Great fit!"
    assert review.user == user


@pytest.mark.django_db
def test_customer_cannot_review_undelivered_order():
    user = UserFactory()
    product = ProductFactory()
    order_item = OrderItemFactory(product=product, order__user=user, order__status=Order.Status.PROCESSING)

    client = _client_for(user)
    response = client.get(reverse("web-product-detail", args=[product.slug]))
    assert b"Rate your purchase" not in response.content

    client.post(reverse("web-product-review-add", args=[product.slug]), {
        "order_item_id": order_item.id, "rating": "5", "comment": "",
    })
    assert not Review.objects.filter(order_item=order_item).exists()


@pytest.mark.django_db
def test_customer_cannot_review_the_same_purchase_twice():
    user = UserFactory()
    product = ProductFactory()
    order_item = OrderItemFactory(product=product, order__user=user, order__status=Order.Status.DELIVERED)
    Review.objects.create(user=user, product=product, order_item=order_item, rating=4)

    client = _client_for(user)
    response = client.get(reverse("web-product-detail", args=[product.slug]))
    assert b"Rate your purchase" not in response.content
