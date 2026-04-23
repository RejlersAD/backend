from django.urls import path

from . import batch_views, views

urlpatterns = [
    # Existing single-file workflow (unchanged)
    path('upload/', views.upload_non_teff_file, name='non-teff-upload'),
    path('status/<str:job_id>/', views.get_non_teff_status, name='non-teff-status'),
    path('results/<str:job_id>/', views.get_non_teff_results, name='non-teff-results'),
    path('export/<str:job_id>/', views.export_non_teff_excel, name='non-teff-export'),

    # Bulk Master Index workflow (additive)
    path('batch/template/', batch_views.get_batch_template, name='non-teff-batch-template'),
    path('batch/create/',   batch_views.create_batch,       name='non-teff-batch-create'),
    path('batch/<uuid:batch_id>/upload/',                batch_views.upload_batch_files,  name='non-teff-batch-upload'),
    path('batch/<uuid:batch_id>/start/',                 batch_views.start_batch,         name='non-teff-batch-start'),
    path('batch/<uuid:batch_id>/status/',                batch_views.batch_status,        name='non-teff-batch-status'),
    path('batch/<uuid:batch_id>/items/',                 batch_views.list_batch_items,    name='non-teff-batch-items'),
    path('batch/<uuid:batch_id>/items/<uuid:item_id>/',  batch_views.update_batch_item,   name='non-teff-batch-item-update'),
    path('batch/<uuid:batch_id>/bulk-update/',           batch_views.bulk_update_items,   name='non-teff-batch-bulk-update'),
    path('batch/<uuid:batch_id>/export/',                batch_views.export_batch,        name='non-teff-batch-export'),
]
