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

    # Document Search Canvas (additive — read-only locator + page renderer)
    path('search/',                                                          batch_views.search_documents,    name='non-teff-search'),
    path('batch/<uuid:batch_id>/items/<uuid:item_id>/locate/',               batch_views.locate_in_item,      name='non-teff-search-locate'),
    path('batch/<uuid:batch_id>/items/<uuid:item_id>/page/<int:page_no>/image/', batch_views.get_item_page_image, name='non-teff-search-page-image'),
    path('batch/<uuid:batch_id>/items/<uuid:item_id>/recommend/',            batch_views.recommend_for_item,  name='non-teff-item-recommend'),
    path('batch/<uuid:batch_id>/items/<uuid:item_id>/yellow/',               batch_views.yellow_regions_for_item, name='non-teff-item-yellow'),
    path('batch/<uuid:batch_id>/coverage/',                                  batch_views.batch_coverage,      name='non-teff-batch-coverage'),
    path('batch/<uuid:batch_id>/reconcile/',                                 batch_views.batch_reconcile,     name='non-teff-batch-reconcile'),

    # Direct-link to original drawing/record (additive — saves search time)
    path('batch/<uuid:batch_id>/items/<uuid:item_id>/file/',                 batch_views.download_item_file,  name='non-teff-item-file'),
    path('batch/<uuid:batch_id>/items/<uuid:item_id>/location/',             batch_views.item_location,       name='non-teff-item-location'),

    # SmartPlant Foundation integration (additive — config-driven)
    path('smartplant/status/',                              batch_views.smartplant_status, name='non-teff-smartplant-status'),
    path('batch/<uuid:batch_id>/smartplant/status/',        batch_views.smartplant_status, name='non-teff-smartplant-batch-status'),
    path('batch/<uuid:batch_id>/smartplant/push/',          batch_views.smartplant_push,   name='non-teff-smartplant-push'),

    # History (additive — role-based, S3-backed, doesn't touch core logic)
    path('history/',                  views.list_non_teff_history, name='non-teff-history-list'),
    path('history/<str:job_id>/',     views.load_non_teff_history, name='non-teff-history-load'),
    path('history/<str:job_id>/delete/', views.delete_non_teff_history, name='non-teff-history-delete'),
    path('history/<str:job_id>/update/', views.update_non_teff_history, name='non-teff-history-update'),
]
