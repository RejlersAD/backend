"""Reviewed server-folder identity registration, with no automatic publication."""

import json

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from apps.file_replica.project_registration import register_server_projects


class Command(BaseCommand):
    help = 'Register canonical project names/codes from discovered server folders. Dry-run unless --apply is supplied.'

    def add_arguments(self, parser):
        parser.add_argument('--source', required=True, help='Existing file server source UUID.')
        parser.add_argument('--actor', required=True, help='Authorized administrator email address or user ID.')
        parser.add_argument('--apply', action='store_true', help='Apply the reviewed identity registrations and folder mappings.')

    def handle(self, *args, **options):
        User = get_user_model()
        reference = str(options['actor']).strip()
        lookup = Q(email__iexact=reference)
        try:
            lookup |= Q(pk=User._meta.pk.to_python(reference))
        except (ValidationError, ValueError, TypeError):
            pass
        actors = list(User.objects.filter(lookup)[:2])
        if len(actors) != 1:
            raise CommandError('The actor must identify exactly one existing user by email or ID.')
        try:
            report = register_server_projects(source_id=options['source'], actor=actors[0], apply=options['apply'])
        except (PermissionDenied, ValidationError) as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(report, indent=2))
