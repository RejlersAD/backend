from django.core.management.base import BaseCommand
from apps.rbac.models import Module, Permission
from apps.rbac.module_actions import ensure_module_actions


class Command(BaseCommand):
    help = 'Complete the six-action module catalogue without changing any grants.'

    def handle(self, *args, **options):
        added = ensure_module_actions(Module, Permission)
        self.stdout.write(self.style.SUCCESS(f'Added {added} missing module action definitions.'))
