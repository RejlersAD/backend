import apps.core.models
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_enquiry_approval_required_enquiry_approval_status_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EnquiryAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('file', models.FileField(max_length=500, upload_to=apps.core.models.enquiry_attachment_upload_to)),
                ('original_name', models.CharField(max_length=255)),
                ('content_type', models.CharField(blank=True, default='', max_length=120)),
                ('size', models.PositiveBigIntegerField(default=0)),
                ('enquiry', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='attachments', to='core.enquiry')),
                ('uploaded_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='enquiry_attachments', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['created_at', 'id']},
        ),
    ]
