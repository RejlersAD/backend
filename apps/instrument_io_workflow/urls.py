from django.urls import path, include
from rest_framework.routers import DefaultRouter

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

    # Legend sheets — lookup/sections/add/edit/delete must come before the
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

    path('',        include(router.urls)),
]
