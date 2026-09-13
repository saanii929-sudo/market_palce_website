"""Shared "one default per owner" bookkeeping for models with a `user` FK and
an `is_default` flag (Address, PaymentMethodToken). Kept here instead of
duplicated per app since both need identical rules:

- The first item a user creates automatically becomes their default (the UI,
  e.g. Checkout, assumes a default always exists once at least one exists).
- Deleting the default promotes another item to default, if one exists.
- Deleting a user's only item is blocked - there would be nothing to
  promote, and Checkout should never see zero-but-should-have-a-default.
"""


class CannotDeleteOnlyDefaultError(Exception):
    def __init__(self, message: str = "You can't delete your only saved item. Add a replacement first."):
        self.message = message
        super().__init__(message)


def assign_default_on_create(instance) -> None:
    model = type(instance)
    has_others = model.objects.filter(user=instance.user).exclude(id=instance.id).exists()
    if not has_others and not instance.is_default:
        instance.is_default = True
        instance.save(update_fields=["is_default"])


def handle_deletion(instance) -> None:
    """Call before deleting `instance`. Raises CannotDeleteOnlyDefaultError if
    it's the user's only one; otherwise promotes another to default first."""
    model = type(instance)
    siblings = model.objects.filter(user=instance.user).exclude(id=instance.id)

    if not siblings.exists():
        raise CannotDeleteOnlyDefaultError()

    if instance.is_default:
        promoted = siblings.order_by("-created_at").first()
        promoted.is_default = True
        promoted.save(update_fields=["is_default"])
