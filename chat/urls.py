from django.urls import path

from . import views

urlpatterns = [
    path("chat/conversations/", views.ConversationListView.as_view(), name="chat-conversation-list"),
    path("chat/conversations/start/", views.ConversationStartView.as_view(), name="chat-conversation-start"),
    path(
        "chat/conversations/<int:conversation_id>/messages/",
        views.ConversationMessageListCreateView.as_view(),
        name="chat-message-list",
    ),
    path(
        "chat/conversations/<int:conversation_id>/read/",
        views.ConversationMarkReadView.as_view(),
        name="chat-conversation-read",
    ),
]
