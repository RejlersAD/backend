"""Protect durable evidence/approved snapshots against bulk-update bypasses."""
from django.db import migrations


def install(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute("""
        CREATE OR REPLACE FUNCTION radai_protect_planning_evidence() RETURNS trigger AS $$
        BEGIN
          IF TG_TABLE_NAME = 'planning_intelligence_evidencedecision' THEN
            RAISE EXCEPTION 'Evidence decisions are append-only';
          ELSIF TG_TABLE_NAME = 'planning_intelligence_evidencedocumentversion' THEN
            RAISE EXCEPTION 'Original evidence versions are immutable';
          ELSIF TG_TABLE_NAME = 'planning_intelligence_evidencenode' THEN
            IF TG_OP = 'DELETE' OR
              (to_jsonb(OLD) - ARRAY['status','current','confidence','validation']) IS DISTINCT FROM
              (to_jsonb(NEW) - ARRAY['status','current','confidence','validation']) THEN
              RAISE EXCEPTION 'Evidence assertions are immutable; record a correction';
            END IF;
          ELSIF TG_TABLE_NAME = 'planning_intelligence_schedulebaseline' AND OLD.approved_at IS NOT NULL THEN
            IF TG_OP = 'DELETE' OR
              (to_jsonb(OLD) - ARRAY['updated_at']) IS DISTINCT FROM
              (to_jsonb(NEW) - ARRAY['updated_at']) THEN
              RAISE EXCEPTION 'Approved baselines are immutable';
            END IF;
          END IF;
          IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
    """)
    for model in ('evidencedecision', 'evidencedocumentversion', 'evidencenode', 'schedulebaseline'):
        table = 'planning_intelligence_' + model
        schema_editor.execute(f'CREATE TRIGGER protect_{model} BEFORE UPDATE OR DELETE ON {table} '
                              'FOR EACH ROW EXECUTE FUNCTION radai_protect_planning_evidence()')


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for model in ('evidencedecision', 'evidencedocumentversion', 'evidencenode', 'schedulebaseline'):
        schema_editor.execute(f'DROP TRIGGER IF EXISTS protect_{model} ON planning_intelligence_{model}')
    schema_editor.execute('DROP FUNCTION IF EXISTS radai_protect_planning_evidence()')


class Migration(migrations.Migration):
    dependencies = [('planning_intelligence', '0034_evidence_graph')]
    operations = [migrations.RunPython(install, uninstall)]
