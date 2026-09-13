import factory
from factory.django import DjangoModelFactory

from support.models import FAQ


class FAQFactory(DjangoModelFactory):
    class Meta:
        model = FAQ

    question = factory.Sequence(lambda n: f"Question {n}?")
    answer = "Here's the answer."
    topic = FAQ.Topic.ORDERS
