"""
One-time fix for production: seeded demo riders were created with
is_online=True and hardcoded Accra coordinates, which caused
find_nearest_eligible_rider() to pick them instead of real riders and
send dispatch WebSocket frames to non-existent sockets.

Run once on the production server:
    python manage.py reset_demo_riders
"""
from django.core.management.base import BaseCommand

DEMO_RIDER_EMAILS = [
    "rider1@example.com",
    "rider2@example.com",
    "rider3@example.com",
    "rider4@example.com",
    "rider5@example.com",
]


class Command(BaseCommand):
    help = "Marks all seeded demo riders offline and clears their coordinates so they are never matched in dispatch."

    def handle(self, *args, **options):
        from riders.models import RiderProfile

        updated = RiderProfile.objects.filter(
            user__email__in=DEMO_RIDER_EMAILS,
        ).update(is_online=False, current_lat=None, current_lng=None)

        self.stdout.write(
            self.style.SUCCESS(
                f"Reset {updated} demo rider(s) — marked offline, coordinates cleared."
            )
        )
