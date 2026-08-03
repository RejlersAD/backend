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
    LineListUploadView,
    LineListDetailView,
    LineListActivateView,
    CrossCheckView,
    EquipmentListUploadView,
    EquipmentListDetailView,
    EquipmentListActivateView,
    EquipmentCrossCheckView,
    InstrumentIndexUploadView,
    InstrumentIndexDetailView,
    InstrumentIndexActivateView,
    InstrumentCrossCheckView,
    ExtractEquipmentTagsFromPidView,
    ExtractInstrumentTagsFromPidView,
    ExtractInstrumentTagsStatusView,
    UsageLogListView,
    UsageSummaryView,
    TokenReportView,
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
LINE_LISTS_LIST_PATH = 'line-lists/'
LINE_LISTS_DETAIL_PATH = 'line-lists/<uuid:line_list_id>/'
LINE_LISTS_ACTIVATE_PATH = 'line-lists/<uuid:line_list_id>/activate/'
CROSS_CHECK_PATH = 'cross-check/'
EQUIPMENT_LISTS_LIST_PATH = 'equipment-lists/'
EQUIPMENT_LISTS_DETAIL_PATH = 'equipment-lists/<uuid:equipment_list_id>/'
EQUIPMENT_LISTS_ACTIVATE_PATH = 'equipment-lists/<uuid:equipment_list_id>/activate/'
EQUIPMENT_CROSS_CHECK_PATH = 'equipment-cross-check/'
INSTRUMENT_INDEXES_LIST_PATH = 'instrument-indexes/'
INSTRUMENT_INDEXES_DETAIL_PATH = 'instrument-indexes/<uuid:instrument_index_id>/'
INSTRUMENT_INDEXES_ACTIVATE_PATH = 'instrument-indexes/<uuid:instrument_index_id>/activate/'
INSTRUMENT_CROSS_CHECK_PATH = 'instrument-cross-check/'
EXTRACT_EQUIPMENT_TAGS_PATH = 'extract-equipment-tags/'
EXTRACT_INSTRUMENT_TAGS_PATH = 'extract-instrument-tags/'
EXTRACT_INSTRUMENT_TAGS_STATUS_PATH = 'extract-instrument-tags/status/<str:job_id>/'
USAGE_LIST_PATH = 'usage/'
USAGE_SUMMARY_PATH = 'usage/summary/'
USAGE_REPORT_PATH = 'usage/report/'

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
    # Master Line List (Excel)
    path(LINE_LISTS_LIST_PATH, LineListUploadView.as_view(),
         name='line-lists'),
    path(LINE_LISTS_ACTIVATE_PATH, LineListActivateView.as_view(),
         name='line-lists-activate'),
    path(LINE_LISTS_DETAIL_PATH, LineListDetailView.as_view(),
         name='line-lists-detail'),
    # Cross-check P&ID tags vs active Line List
    path(CROSS_CHECK_PATH, CrossCheckView.as_view(), name='cross-check'),
    # Master Equipment List (Excel)
    path(EQUIPMENT_LISTS_LIST_PATH, EquipmentListUploadView.as_view(),
         name='equipment-lists'),
    path(EQUIPMENT_LISTS_ACTIVATE_PATH, EquipmentListActivateView.as_view(),
         name='equipment-lists-activate'),
    path(EQUIPMENT_LISTS_DETAIL_PATH, EquipmentListDetailView.as_view(),
         name='equipment-lists-detail'),
    # Cross-check P&ID equipment tags vs active Equipment List
    path(EQUIPMENT_CROSS_CHECK_PATH, EquipmentCrossCheckView.as_view(),
         name='equipment-cross-check'),
    # Master Instrument Index (Excel)
    path(INSTRUMENT_INDEXES_LIST_PATH, InstrumentIndexUploadView.as_view(),
         name='instrument-indexes'),
    path(INSTRUMENT_INDEXES_ACTIVATE_PATH, InstrumentIndexActivateView.as_view(),
         name='instrument-indexes-activate'),
    path(INSTRUMENT_INDEXES_DETAIL_PATH, InstrumentIndexDetailView.as_view(),
         name='instrument-indexes-detail'),
    # Cross-check P&ID instrument tags vs active Instrument Index
    path(INSTRUMENT_CROSS_CHECK_PATH, InstrumentCrossCheckView.as_view(),
         name='instrument-cross-check'),
    # Dedicated Vision extractors used by cross-check panels (BYOK)
    path(EXTRACT_EQUIPMENT_TAGS_PATH, ExtractEquipmentTagsFromPidView.as_view(),
         name='extract-equipment-tags'),
    path(EXTRACT_INSTRUMENT_TAGS_STATUS_PATH, ExtractInstrumentTagsStatusView.as_view(),
         name='extract-instrument-tags-status'),
    path(EXTRACT_INSTRUMENT_TAGS_PATH, ExtractInstrumentTagsFromPidView.as_view(),
         name='extract-instrument-tags'),
    # Token usage / consolidated report — /usage/summary/ must precede /usage/
    path(USAGE_SUMMARY_PATH, UsageSummaryView.as_view(), name='usage-summary'),
    path(USAGE_REPORT_PATH, TokenReportView.as_view(), name='usage-report'),
    path(USAGE_LIST_PATH, UsageLogListView.as_view(), name='usage-list'),
]
