from django.db import models

from core.models import TimeStampedModel


class StaticPage(TimeStampedModel):
    class Slug(models.TextChoices):
        ABOUT = "about", "About"
        SHIPPING = "shipping", "Shipping"
        RETURNS = "returns", "Returns"
        PRIVACY = "privacy", "Privacy"
        TERMS = "terms", "Terms"

    slug = models.CharField(max_length=20, choices=Slug.choices, unique=True)
    title = models.CharField(max_length=150)
    body = models.TextField(help_text="Rich text / HTML rendered on the footer page.")

    def __str__(self):
        return self.title
