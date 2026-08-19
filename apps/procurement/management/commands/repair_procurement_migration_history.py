"""Safely align known Procurement migration aliases after branch drift.

This command never executes migration operations.  It only records the current
0020-0022 migration names after proving that their schema effects are already
present and that later repair migrations were previously recorded.
"""

from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.db.migrations.exceptions import InconsistentMigrationHistory
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder


CANONICAL_MIGRATIONS = (
    "0020_rename_current_approval_level_to_step",
    "0021_pr_feedback_workflow_fields",
    "0022_rename_procurement_budget_idx1_procurement_project_cf65fc_idx_and_more",
)

REQUIRED_LATER_REPAIRS = (
    "0026_repair_purchase_requisition_columns",
    "0027_repair_legacy_approval_step",
)

EXPECTED_COLUMNS = {
    "current_approval_step": "integer",
    "management_approval": "boolean",
    "management_approval_evidence": "jsonb",
    "management_approval_remarks": "text",
    "po_applicable": "boolean",
    "project_details": "jsonb",
    "resolution_referral": "jsonb",
    "review_due_at": "timestamp with time zone",
    "selected_vendors": "jsonb",
    "single_source_justification": "text",
}

EXPECTED_INDEXES = {
    "procurement_project_cf65fc_idx": ("procurement_budget", "project_id, category"),
    "procurement_fiscal__0de50e_idx": ("procurement_budget", "fiscal_year, is_approved"),
    "procurement_code_f170f4_idx": ("procurement_costcenter", "code, is_active"),
    "procurement_departm_ea9b87_idx": ("procurement_costcenter", "department, division"),
    "procurement_project_3cb8c1_idx": ("procurement_project", "project_number"),
    "procurement_status_5adfc7_idx": ("procurement_project", "status, is_active"),
    "procurement_client__88c69c_idx": ("procurement_project", "client_name, project_type"),
    "procurement_start_d_19f6a3_idx": ("procurement_project", "start_date, planned_end_date"),
}


def validate_schema(columns, indexes):
    """Return human-readable blockers for the schema effects being faked."""
    blockers = []
    for name, expected_type in EXPECTED_COLUMNS.items():
        actual = columns.get(name)
        if actual is None:
            blockers.append(f"Missing column procurement_requisitions.{name}.")
        elif actual != expected_type:
            blockers.append(
                f"Column procurement_requisitions.{name} is {actual}, expected {expected_type}."
            )

    for name, (expected_table, expected_columns) in EXPECTED_INDEXES.items():
        actual = indexes.get(name)
        if actual is None:
            blockers.append(f"Missing index {name}.")
            continue
        table, definition = actual
        normalized_definition = " ".join(definition.lower().replace('"', "").split())
        if table != expected_table or f"({expected_columns})" not in normalized_definition:
            blockers.append(
                f"Index {name} does not match {expected_table}({expected_columns})."
            )
    return blockers


class Command(BaseCommand):
    help = (
        "Dry-run or atomically repair known Procurement migration-history aliases "
        "after verifying their schema effects."
    )

    def add_arguments(self, parser):
        parser.add_argument("--database", default="default")
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Record the verified migration names. Without this flag the command is read-only.",
        )
        parser.add_argument(
            "--confirm-target",
            default="",
            help="Required with --apply; must exactly match the target printed by dry-run.",
        )

    @staticmethod
    def _target_fingerprint(connection):
        settings = connection.settings_dict
        return f"{settings.get('HOST')}:{settings.get('PORT')}/{settings.get('NAME')}"

    @staticmethod
    def _schema_snapshot(connection):
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT column_name, data_type
                  FROM information_schema.columns
                 WHERE table_schema = current_schema()
                   AND table_name = 'procurement_requisitions'
                """
            )
            columns = dict(cursor.fetchall())
            cursor.execute(
                """
                SELECT indexname, tablename, indexdef
                  FROM pg_indexes
                 WHERE schemaname = current_schema()
                   AND tablename IN (
                       'procurement_budget', 'procurement_costcenter', 'procurement_project'
                   )
                """
            )
            indexes = {name: (table, definition) for name, table, definition in cursor.fetchall()}
        return columns, indexes

    def handle(self, *args, **options):
        database = options["database"]
        connection = connections[database]
        target = self._target_fingerprint(connection)
        recorder = MigrationRecorder(connection)
        applied = {
            name
            for app, name in recorder.applied_migrations()
            if app == "procurement"
        }
        missing = [name for name in CANONICAL_MIGRATIONS if name not in applied]

        self.stdout.write(f"Database target: {target}")
        self.stdout.write("Mode: APPLY" if options["apply"] else "Mode: DRY RUN (read-only)")
        self.stdout.write(f"Missing canonical records: {', '.join(missing) if missing else 'none'}")

        if not missing:
            self.stdout.write(self.style.SUCCESS("Procurement migration history is already aligned."))
            return

        missing_later = [name for name in REQUIRED_LATER_REPAIRS if name not in applied]
        if missing_later:
            raise CommandError(
                "This is not the known branch-drift case; later repair migrations are absent: "
                + ", ".join(missing_later)
            )

        if connection.vendor != "postgresql":
            raise CommandError("The verified repair currently supports PostgreSQL only.")

        columns, indexes = self._schema_snapshot(connection)
        blockers = validate_schema(columns, indexes)
        if blockers:
            for blocker in blockers:
                self.stderr.write(self.style.ERROR(f"BLOCKER: {blocker}"))
            raise CommandError("Schema verification failed; no migration records were changed.")

        self.stdout.write(self.style.SUCCESS("Schema verification passed."))
        self.stdout.write("Existing migration rows and Procurement business data will not be modified.")
        if not options["apply"]:
            self.stdout.write(
                self.style.WARNING(
                    f"To apply: add --apply --confirm-target '{target}'"
                )
            )
            return

        if options["confirm_target"] != target:
            raise CommandError(
                "Target confirmation does not match. Run without --apply and copy the exact target."
            )

        try:
            with transaction.atomic(using=database):
                # Only insert the missing aliases. Nothing is updated or deleted, so the
                # exact rollback set is the list printed above.
                for name in missing:
                    recorder.record_applied("procurement", name)
                MigrationLoader(connection).check_consistent_history(connection)
        except InconsistentMigrationHistory as exc:
            raise CommandError(f"Repair rolled back because history remains inconsistent: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Recorded {len(missing)} verified migration alias(es) atomically."
            )
        )
        self.stdout.write("Next action: run the normal Procurement migration plan.")
