"""Revert the DB-based "global default" mechanism added in 0011.

Defaults now live as repo-committed static files (see
apps/pid_checker_v2/services/default_symbol_images.py) instead of
project=NULL rows in this table, so a fresh server with an empty database
still has every default picture — no seeding required. This table goes
back to holding only project-specific uploads.

Safe to run: confirmed zero project=NULL rows exist before this migration
was written (the DB-defaults feature was never actually used end-to-end).
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pid_checker_v2', '0011_legend_symbol_image_defaults_and_s3'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='legendsymbolimage',
            name='uniq_pidv2_symbolimage_default_per_symbol',
        ),
        migrations.RemoveField(
            model_name='legendsymbolimage',
            name='is_default',
        ),
        migrations.AlterField(
            model_name='legendsymbolimage',
            name='project',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='legend_symbol_images', to='pid_verification.pidvproject'),
        ),
    ]
