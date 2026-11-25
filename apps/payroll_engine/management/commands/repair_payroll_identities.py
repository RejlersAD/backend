from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count

from apps.payroll_engine.models import PayrollAdjustment, PayrollEmployee
from apps.payroll_engine.services.calculator import recompute_run_totals


class Command(BaseCommand):
    help = (
        'Audit duplicate active canonical payroll identities. With --apply, keep the '
        'record matching EmployeeMaster.employee_number, deactivate duplicates, remove '
        'their Draft payslips, and align linked active payroll employee numbers.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    @transaction.atomic
    def handle(self, *args, **options):
        apply_changes = options['apply']
        groups = list(
            PayrollEmployee.objects.filter(is_active=True, employee_id__isnull=False)
            .values('employee_id').annotate(total=Count('id')).filter(total__gt=1)
        )
        self.stdout.write(f'Duplicate active canonical identities: {len(groups)}')
        changed_runs = set()
        deactivated = 0

        for group in groups:
            records = list(
                PayrollEmployee.objects.select_for_update().select_related('employee')
                .filter(is_active=True, employee_id=group['employee_id']).order_by('id')
            )
            canonical_no = records[0].employee.employee_number.strip()
            matches = [p for p in records if p.employee_no.strip().casefold() == canonical_no.casefold()]
            if len(matches) != 1:
                raise CommandError(
                    f'Canonical employee {group["employee_id"]} has no unique payroll record '
                    f'matching employee number {canonical_no}; manual review required.'
                )
            survivor = matches[0]
            duplicates = [p for p in records if p.pk != survivor.pk]
            self.stdout.write(
                f'Canonical {canonical_no}: keep payroll #{survivor.pk}; '
                f'deactivate {[p.pk for p in duplicates]}'
            )
            if not apply_changes:
                continue
            for duplicate in duplicates:
                PayrollAdjustment.objects.filter(employee=duplicate, status='pending').update(employee=survivor)
                draft_slips = duplicate.payslips.filter(run__status='draft')
                changed_runs.update(draft_slips.values_list('run_id', flat=True))
                draft_slips.delete()
                duplicate.is_active = False
                duplicate.save(update_fields=['is_active', 'updated_at'])
                deactivated += 1

        if apply_changes:
            linked = PayrollEmployee.objects.select_for_update().select_related('employee').filter(
                is_active=True, employee_id__isnull=False,
            )
            for record in linked:
                canonical_no = record.employee.employee_number.strip()
                if record.employee_no == canonical_no:
                    continue
                if PayrollEmployee.objects.exclude(pk=record.pk).filter(employee_no=canonical_no).exists():
                    raise CommandError(
                        f'Cannot align payroll #{record.pk} to {canonical_no}: that number already exists.'
                    )
                record.employee_no = canonical_no
                record.save(update_fields=['employee_no', 'updated_at'])

            from apps.payroll_engine.models import PayrollRun
            for run in PayrollRun.objects.select_for_update().filter(pk__in=changed_runs):
                recompute_run_totals(run)
                run.save()
            self.stdout.write(self.style.SUCCESS(
                f'Applied: deactivated {deactivated} duplicate payroll record(s); '
                f'recalculated {len(changed_runs)} Draft run(s). Released runs were unchanged.'
            ))
        else:
            self.stdout.write('Dry run only. Re-run with --apply after reviewing the report.')
