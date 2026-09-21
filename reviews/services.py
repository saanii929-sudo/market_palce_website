from django.db import transaction

from .models import Review, ReviewFlag

FLAG_THRESHOLD = 3


class ReviewModerationError(Exception):
    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


@transaction.atomic
def flag_review(*, review: Review, flagged_by, reason: str) -> ReviewFlag:
    if reason not in ReviewFlag.Reason.values:
        raise ReviewModerationError("Please select a valid reason.")
    if review.user_id == flagged_by.id:
        raise ReviewModerationError("You can't flag your own review.")
    if ReviewFlag.objects.filter(review=review, flagged_by=flagged_by).exists():
        raise ReviewModerationError("You've already flagged this review.")

    flag = ReviewFlag.objects.create(review=review, flagged_by=flagged_by, reason=reason)

    review.flagged_count = review.flags.count()
    update_fields = ["flagged_count"]
    if review.flagged_count >= FLAG_THRESHOLD and review.status == Review.Status.VISIBLE:
        review.status = Review.Status.FLAGGED
        update_fields.append("status")
    review.save(update_fields=update_fields)

    return flag


def moderate_review(*, review: Review, action: str, moderation_note: str = "") -> Review:
    if action == "keep":
        new_status = Review.Status.VISIBLE
    elif action == "remove":
        new_status = Review.Status.REMOVED
    else:
        raise ReviewModerationError("Unknown moderation action - use 'keep' or 'remove'.")

    review.status = new_status
    review.moderation_note = moderation_note
    review.save(update_fields=["status", "moderation_note"])
    return review
