from django.core.management.base import BaseCommand

from apps.hr_core.identity import EmployeeIdentityService
from apps.hr_core.models import EmployeeIdentityAlias, EmployeeMaster


class Command(BaseCommand):
    help = 'Build the canonical cross-system identity alias registry.'

    def add_arguments(self, parser):
        parser.add_argument('--employee', help='Canonical UUID, user ID, email, or employee code')

    def handle(self, *args, **options):
        if options.get('employee'):
            employee = EmployeeIdentityService.resolve(options['employee'])
            employees = EmployeeMaster.objects.filter(pk=employee.pk) if employee else EmployeeMaster.objects.none()
        else:
            employees = EmployeeMaster.objects.select_related('user').all()
        processed = 0
        conflicts = []
        for employee in employees.iterator():
            results = EmployeeIdentityService.register_aliases(employee)
            conflicts.extend(item for item in results if isinstance(item, dict))
            processed += 1
        self.stdout.write(self.style.SUCCESS(
            f'Processed {processed} employees; {EmployeeIdentityAlias.objects.count()} aliases registered.'
        ))
        if conflicts:
            self.stdout.write(self.style.WARNING(f'{len(conflicts)} identifier conflict(s) require review.'))
