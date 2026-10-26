from django.urls import path, include
from rest_framework.routers import DefaultRouter

from . import views_extraction as extraction_views
from apps.electrical_comparison.views_staged import SldUserAIKeyTestView, SldUserAIKeyView
from .views import (
    IOListProjectViewSet, IOListDocumentViewSet, config_view, diff_view,
    vision_test_key_view, pdf_page_count_view,
    IOListLegendSheetListCreateView, IOListLegendSheetDetailView,
    IOListLegendActivateView,
    LegendLookupAddView, LegendLookupEditView, LegendLookupDeleteView,
    LegendLookupSectionsView,
    SymbolImagesListView, SymbolImageUploadView, SymbolImageDeleteView,
    DefaultSymbolImagesView,
)

app_name = 'instrument_io_workflow'

router = DefaultRouter()
router.register(r'projects', IOListProjectViewSet, basename='io-project')
router.register(r'documents', IOListDocumentViewSet, basename='io-document')

urlpatterns = [
    path('config/', config_view, name='config'),
    path('diff/',   diff_view,   name='diff'),
    path('vision/test-key/', vision_test_key_view, name='vision-test-key'),
    path('pdf-page-count/', pdf_page_count_view, name='pdf-page-count'),

    # Legend sheets â€” lookup/sections/add/edit/delete must come before the
    # <uuid:legend_id> detail pattern.
    path('legends/add-lookup/', LegendLookupAddView.as_view(), name='legend-lookup-add'),
    path('legends/edit-lookup/', LegendLookupEditView.as_view(), name='legend-lookup-edit'),
    path('legends/delete-lookup/', LegendLookupDeleteView.as_view(), name='legend-lookup-delete'),
    path('legends/lookup-sections/', LegendLookupSectionsView.as_view(), name='legend-lookup-sections'),
    path('legends/', IOListLegendSheetListCreateView.as_view(), name='legends-list'),
    path('legends/<uuid:legend_id>/activate/', IOListLegendActivateView.as_view(), name='legends-activate'),
    path('legends/<uuid:legend_id>/', IOListLegendSheetDetailView.as_view(), name='legends-detail'),

    # Legend symbol reference pictures
    path('symbol-images/', SymbolImagesListView.as_view(), name='symbol-images'),
    path('symbol-image/upload/', SymbolImageUploadView.as_view(), name='symbol-image-upload'),
    path('symbol-image/delete/', SymbolImageDeleteView.as_view(), name='symbol-image-delete'),
    path('default-symbol-images/', DefaultSymbolImagesView.as_view(), name='default-symbol-images'),

    # Deterministic P&ID â†’ IO List extraction (alignment spec Â§8)
    # BYOK key endpoints reuse the shared per-user AI key views (the key is
    # stored per user, not per feature).
    path('user/ai-key/', SldUserAIKeyView.as_view(), name='io-ai-key'),
    path('user/ai-key/test/', SldUserAIKeyTestView.as_view(), name='io-ai-key-test'),
    path('projects/<int:project_id>/extraction-files/',
         extraction_views.upload_extraction_file, name='extraction-file-upload'),
    path('projects/<int:project_id>/extraction-files/list/',
         extraction_views.list_extraction_files, name='extraction-file-list'),
    path('projects/<int:project_id>/extraction-legend/',
         extraction_views.put_legend, name='extraction-legend'),
    path('projects/<int:project_id>/extraction-doc-info/',
         extraction_views.put_doc_info, name='extraction-doc-info'),
    path('projects/<int:project_id>/extract/',
         extraction_views.start_extraction, name='extraction-start'),
    path('projects/<int:project_id>/extractions/<uuid:extraction_id>/status/',
         extraction_views.extraction_status, name='extraction-status'),
    path('projects/<int:project_id>/extractions/<uuid:extraction_id>/decisions/',
         extraction_views.post_decisions, name='extraction-decisions'),
    path('projects/<int:project_id>/extractions/<uuid:extraction_id>/ai/<str:loop_key>/',
         extraction_views.ai_suggest, name='extraction-ai'),
    path('projects/<int:project_id>/extractions/<uuid:extraction_id>/export/',
         extraction_views.export_extraction, name='extraction-export'),
    path('projects/<int:project_id>/extractions/<uuid:extraction_id>/',
         extraction_views.get_extraction, name='extraction-detail'),

    path('',        include(router.urls)),
]
