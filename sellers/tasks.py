from celery import shared_task


@shared_task
def process_bulk_upload(job_id: int) -> None:
    from .bulk_upload import run_bulk_upload

    run_bulk_upload(job_id)
