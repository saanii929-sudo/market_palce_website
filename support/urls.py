from django.urls import path

from . import views

urlpatterns = [
    path("support/faqs/", views.FAQListView.as_view(), name="faq-list"),
    path("support/faqs/search/", views.FAQSearchView.as_view(), name="faq-search"),
    path("support/tickets/", views.SupportTicketCreateView.as_view(), name="support-ticket-create"),
    path("support/contacts/", views.SupportContactListView.as_view(), name="support-contact-list"),
]
