import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from accounts.tests.factories import UserFactory

from ..models import FAQ, SupportContact, SupportTicket
from .factories import FAQFactory


@pytest.fixture
def api_client():
    return APIClient()


@pytest.mark.django_db
def test_faq_list_filters_by_topic(api_client):
    FAQFactory(question="How do I track my order?", topic=FAQ.Topic.ORDERS)
    FAQFactory(question="How do I pay?", topic=FAQ.Topic.PAYMENTS)

    response = api_client.get(reverse("faq-list"), {"topic": "orders"})
    questions = [f["question"] for f in response.data["results"]]
    assert questions == ["How do I track my order?"]


@pytest.mark.django_db
def test_faq_list_excludes_inactive(api_client):
    FAQFactory(question="Active", is_active=True)
    FAQFactory(question="Inactive", is_active=False)

    response = api_client.get(reverse("faq-list"))
    questions = [f["question"] for f in response.data["results"]]
    assert "Active" in questions
    assert "Inactive" not in questions


@pytest.mark.django_db
def test_faq_search_matches_question_or_answer(api_client):
    FAQFactory(question="Refund policy", answer="We refund within 30 days.")
    FAQFactory(question="Shipping", answer="We ship worldwide.")

    response = api_client.get(reverse("faq-search"), {"q": "refund"})
    questions = [f["question"] for f in response.data["results"]]
    assert questions == ["Refund policy"]


@pytest.mark.django_db
def test_submit_support_ticket_requires_authentication(api_client):
    response = api_client.post(reverse("support-ticket-create"), {"channel": "email", "subject": "Help", "message": "..."})
    assert response.status_code == 401


@pytest.mark.django_db
def test_submit_support_ticket():
    user = UserFactory(email="ticketer@example.com")
    client = APIClient()
    client.force_authenticate(user=user)

    response = client.post(
        reverse("support-ticket-create"), {"channel": "chat", "subject": "Order issue", "message": "Where is my order?"}
    )
    assert response.status_code == 201
    assert response.data["status"] == SupportTicket.Status.OPEN
    assert SupportTicket.objects.filter(user=user, subject="Order issue").exists()


@pytest.mark.django_db
def test_support_contact_list_is_public_and_active_only(api_client):
    SupportContact.objects.create(kind="email", label="General", value="help@sporttech.example", is_active=True)
    SupportContact.objects.create(kind="phone", label="Retired line", value="+233200000000", is_active=False)

    response = api_client.get(reverse("support-contact-list"))
    assert response.status_code == 200
    labels = [c["label"] for c in response.data]
    assert labels == ["General"]
