from django.db import migrations


def add_default_for_is_hidden(apps, schema_editor):
    """
    Root cause of production "user_profile: This field is required" errors on
    Achievements/Experience/Social Links/Documents:

    The live `rbac_user_profiles` table has an orphan `is_hidden` column
    (NOT NULL, no default) that is not declared anywhere in models.py or any
    other migration — it predates the current codebase and was never wired
    into Django. Since Django doesn't know the column exists, every INSERT
    it issues omits it, and Postgres rejects the row with:

        IntegrityError: null value in column "is_hidden" of relation
        "rbac_user_profiles" violates not-null constraint

    This silently broke auto-creation of UserProfile for any user who didn't
    already have one, which the DRF serializer then surfaced as a generic
    "user_profile is required" 400 error.

    Fix: give the column a server-side default so inserts succeed regardless
    of whether the ORM references it. Idempotent — safe to run on databases
    that don't have this column at all (fresh installs).
    """
    connection = schema_editor.connection
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'rbac_user_profiles' AND column_name = 'is_hidden'
            """
        )
        if cursor.fetchone():
            cursor.execute(
                "ALTER TABLE rbac_user_profiles ALTER COLUMN is_hidden SET DEFAULT false"
            )
            cursor.execute(
                "UPDATE rbac_user_profiles SET is_hidden = false WHERE is_hidden IS NULL"
            )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('rbac', '0044_seed_custom_roles_parity'),
    ]

    operations = [
        migrations.RunPython(add_default_for_is_hidden, noop_reverse),
    ]
