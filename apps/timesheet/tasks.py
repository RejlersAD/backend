"""
Celery task: monthly report email to managers/HR.

Runs on a schedule defined in your celerybeat config. Soft-coded so the
recipient list, subject template, and report month are env-driven.
"""
from __future__ import annotations

import datetime as dt
import logging
import os

from celery import shared_task
from django.conf import settings
from django.core.mail import EmailMessage

from . import config as ts_config
from . import exports as ts_exports

logger = logging.getLogger(__name__)


def _recipients() -> list[str]:
    raw = os.environ.get('TIMESHEET_REPORT_RECIPIENTS', '')
    out = [e.strip() for e in raw.split(',') if e.strip()]
    if out:
        return out
    fallback = getattr(settings, 'HR_ADMIN_EMAIL', None) or getattr(settings, 'DEFAULT_FROM_EMAIL', None)
    return [fallback] if fallback else []


@shared_task(name='timesheet.send_monthly_report')
def send_monthly_report(year: int | None = None, month: int | None = None) -> dict:
    """Generate the monthly Excel + PDF and email both to TIMESHEET_REPORT_RECIPIENTS."""
    if not ts_config.is_configured():
        return {'status': 'skipped', 'reason': 'not_configured'}

    today = dt.date.today()
    y = int(year or today.year)
    m = int(month or today.month)

    to = _recipients()
    if not to:
        return {'status': 'skipped', 'reason': 'no_recipients'}

    xlsx_resp = ts_exports.export_monthly_excel(y, m)
    pdf_resp = ts_exports.export_monthly_pdf(y, m)

    subject = f"[RADAI] Time Sheet — {y}-{m:02d} monthly report"
    body = (
        f"Attached: monthly attendance summary for {y}-{m:02d}.\n"
        f"Generated automatically from {ts_config.SQLSERVER['host']} / "
        f"{ts_config.SQLSERVER['database']}.\n"
    )
    msg = EmailMessage(
        subject=subject,
        body=body,
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
        to=to,
    )
    msg.attach(f'timesheet_{y}_{m:02d}.xlsx',
               xlsx_resp.content,
               'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    msg.attach(f'timesheet_{y}_{m:02d}.pdf', pdf_resp.content, 'application/pdf')
    try:
        msg.send(fail_silently=False)
        return {'status': 'sent', 'recipients': to, 'year': y, 'month': m}
    except Exception as exc:
        logger.exception('[timesheet] monthly report email failed')
        return {'status': 'failed', 'error': str(exc)}
