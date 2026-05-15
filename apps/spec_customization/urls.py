"""Spec Customization — URL routing."""
from django.urls import path

from . import views

app_name = 'spec_customization'

urlpatterns = [
    # Paper Spec extraction
    path('paper-spec/upload/',                 views.upload_paper_spec, name='upload'),
    # Direct-to-S3 presigned upload (large-file path)
    path('paper-spec/upload/presign/',  views.presign_paper_spec_upload,  name='upload-presign'),
    path('paper-spec/upload/complete/', views.complete_paper_spec_upload, name='upload-complete'),
    path('paper-spec/jobs/',                   views.list_jobs,         name='jobs'),
    path('paper-spec/jobs/<uuid:job_id>/',     views.job_detail,        name='job-detail'),
    path('paper-spec/jobs/<uuid:job_id>/classes/', views.job_classes,   name='job-classes'),
    path('paper-spec/jobs/<uuid:job_id>/cancel/',  views.cancel_job,    name='job-cancel'),
    path('paper-spec/jobs/<uuid:job_id>/export/',  views.export_job,    name='job-export'),
    path('paper-spec/jobs/<uuid:job_id>/export-spec/', views.export_smartplant_spec, name='job-export-spec'),
    path('paper-spec/jobs/<uuid:job_id>/export-cat/',  views.export_smartplant_cat,  name='job-export-cat'),
    path('paper-spec/jobs/<uuid:job_id>/workbook/',      views.workbook_preview, name='job-workbook-preview'),
    path('paper-spec/jobs/<uuid:job_id>/workbook/cell/', views.workbook_cell,    name='job-workbook-cell'),
    path('paper-spec/classes/<uuid:class_id>/',    views.class_detail,  name='class-detail'),

    # Config
    path('config/', views.config_view, name='config'),
]
