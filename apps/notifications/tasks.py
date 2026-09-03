"""Celery task discovery for notification delivery channels."""

from .teams import send_teams_approval_assignment


__all__ = ['send_teams_approval_assignment']
