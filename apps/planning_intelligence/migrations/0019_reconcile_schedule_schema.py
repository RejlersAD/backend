from django.db import migrations


# Historical production databases may contain the planning migration records
# while lacking tables introduced by the relational scheduling rollout. Keep
# this list dependency-ordered so a missing table can be created safely from
# the historical model state without rolling migration history backward.
SCHEDULE_MODEL_ORDER = (
    'WorkCalendar',
    'ScheduleResource',
    'Schedule',
    'ScheduleVersion',
    'ScheduleWBSNode',
    'ScheduleActivity',
    'CalendarException',
    'ActivityAssignment',
    'ScheduleBaseline',
    'ActivityRelationship',
    'ScheduleCalculationRun',
    'ActivityProgressUpdate',
    'ScheduleControlSnapshot',
    'GovernanceItem',
    'ScheduleReview',
    'ScheduleReviewDecision',
    'GovernanceComment',
    'ScheduleExportRecord',
    'DailyFieldUpdate',
)


def create_missing_schedule_tables(apps, schema_editor):
    connection = schema_editor.connection
    existing_tables = set(connection.introspection.table_names())

    for model_name in SCHEDULE_MODEL_ORDER:
        model = apps.get_model('planning_intelligence', model_name)
        table_name = model._meta.db_table
        if table_name in existing_tables:
            continue
        schema_editor.create_model(model)
        existing_tables.add(table_name)


class Migration(migrations.Migration):
    dependencies = [
        ('planning_intelligence', '0018_daily_field_updates'),
    ]

    operations = [
        migrations.RunPython(
            create_missing_schedule_tables,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
