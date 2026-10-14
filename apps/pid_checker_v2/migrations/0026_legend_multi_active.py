# Allow multiple active legend sheets per (user, section, project) -
# actives are merged at resolution time (see views._resolve_active_legend).
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('pid_checker_v2', '0025_alter_pidcheckerv2legendsheet_section'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='pidcheckerv2legendsheet',
            name='uniq_pidv2_active_legend_per_user_section_project',
        ),
    ]
