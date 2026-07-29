"""P&ID Checker V2 URL routes."""
from django.urls import path

from .views import (
    ExtractLineTagsView,
    ExtractionListView,
    ExtractionDetailView,
    LegendSheetListCreateView,
    LegendSheetDetailView,
    LegendSheetActivateView,
    LegendSheetDefaultTemplateView,
    ValidateLineTagsView,
)

app_name = 'pid_checker_v2'

# Soft-coded endpoint paths
EXTRACT_LINE_TAGS_PATH = 'extract-line-tags/'
VALIDATE_LINE_TAGS_PATH = 'validate-line-tags/'
EXTRACTIONS_LIST_PATH = 'extractions/'
EXTRACTIONS_DETAIL_PATH = 'extractions/<uuid:extraction_id>/'
LEGENDS_LIST_PATH = 'legends/'
LEGENDS_DETAIL_PATH = 'legends/<uuid:legend_id>/'
LEGENDS_ACTIVATE_PATH = 'legends/<uuid:legend_id>/activate/'
LEGENDS_DEFAULT_TEMPLATE_PATH = 'legends/default-template/'

urlpatterns = [
    path(EXTRACT_LINE_TAGS_PATH, ExtractLineTagsView.as_view(),
         name='extract-line-tags'),
    path(VALIDATE_LINE_TAGS_PATH, ValidateLineTagsView.as_view(),
         name='validate-line-tags'),
    path(EXTRACTIONS_LIST_PATH, ExtractionListView.as_view(),
         name='extractions-list'),
    path(EXTRACTIONS_DETAIL_PATH, ExtractionDetailView.as_view(),
         name='extractions-detail'),
    # Legend Sheets — default-template must be BEFORE the UUID pattern
    path(LEGENDS_DEFAULT_TEMPLATE_PATH, LegendSheetDefaultTemplateView.as_view(),
         name='legends-default-template'),
    path(LEGENDS_LIST_PATH, LegendSheetListCreateView.as_view(),
         name='legends-list'),
    path(LEGENDS_ACTIVATE_PATH, LegendSheetActivateView.as_view(),
         name='legends-activate'),
    path(LEGENDS_DETAIL_PATH, LegendSheetDetailView.as_view(),
         name='legends-detail'),
]
