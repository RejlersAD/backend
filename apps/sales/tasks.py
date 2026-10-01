"""Only mailbox UUIDs enter Celery; source bodies and cursors remain private."""

from time import monotonic

from billiard.exceptions import SoftTimeLimitExceeded
from celery import shared_task

from .mailbox_sync import due_mailbox_ids, run_mailbox_sync


@shared_task(ignore_result=True, soft_time_limit=30, time_limit=45)
def dispatch_mailbox_sync():
    dispatched, started = 0, monotonic()
    for connection_id in due_mailbox_ids():
        if monotonic() - started >= 20:
            break
        try:
            sync_mailbox.delay(str(connection_id))
            dispatched += 1
        except SoftTimeLimitExceeded:
            break
        except Exception:
            # The SQL due row remains available for the next Beat tick.
            break
    return {'dispatched': dispatched}


@shared_task(ignore_result=True, acks_late=True, reject_on_worker_lost=True,
             soft_time_limit=120, time_limit=150)
def sync_mailbox(connection_id):
    return run_mailbox_sync(connection_id)


@shared_task(ignore_result=True, soft_time_limit=30, time_limit=45)
def dispatch_opportunity_workspaces():
    from .opportunity_workspace import due_workspace_ids
    dispatched, started = 0, monotonic()
    for workspace_id in due_workspace_ids():
        if monotonic() - started >= 20:
            break
        try:
            provision_opportunity_workspace.delay(str(workspace_id))
            dispatched += 1
        except Exception:
            break  # SQL rows remain due; no lost in-process-only callback.
    return {'dispatched': dispatched}


@shared_task(ignore_result=True, acks_late=True, reject_on_worker_lost=True,
             soft_time_limit=300, time_limit=360)
def provision_opportunity_workspace(workspace_id):
    from .opportunity_workspace import run_workspace_setup
    return run_workspace_setup(workspace_id)
