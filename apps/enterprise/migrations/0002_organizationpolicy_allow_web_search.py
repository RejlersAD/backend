from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('enterprise', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='organizationpolicy',
            name='allow_web_search',
            field=models.BooleanField(default=False, help_text='Let RADAI Intelligence search the public web (Claude only). Off by default.'),
        ),
    ]
