"""P&ID Checker V2 — extraction + history endpoints.

POST /extract-line-tags/                — upload PDF, auto-save result, return tags
GET  /extractions/                      — list current user's extractions
GET  /extractions/<extraction_id>/      — single extraction with all tags
DELETE /extractions/<extraction_id>/    — delete own extraction
"""
from __future__ import annotations

import hashlib
import logging

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    PidCheckerV2Extraction,
    PidCheckerV2LineTag,
    PidCheckerV2LegendSheet,
    PidCheckerV2LineListUpload,
    PidCheckerV2LineListRow,
    PidCheckerV2EquipmentListUpload,
    PidCheckerV2EquipmentListRow,
    PidCheckerV2InstrumentIndexUpload,
    PidCheckerV2InstrumentIndexRow,
    PidCheckerV2UsageLog,
    MODE_OCR,
    MODE_VISION,
)
from .serializers import (
    PidCheckerV2ExtractionListSerializer,
    PidCheckerV2ExtractionDetailSerializer,
    PidCheckerV2LegendSheetSerializer,
    PidCheckerV2LineListListSerializer,
    PidCheckerV2LineListDetailSerializer,
    PidCheckerV2EquipmentListListSerializer,
    PidCheckerV2EquipmentListDetailSerializer,
    PidCheckerV2InstrumentIndexListSerializer,
    PidCheckerV2InstrumentIndexDetailSerializer,
    PidCheckerV2UsageLogSerializer,
)
from .legend_defaults import (
    SECTIONS as LEGEND_SECTIONS,
    SECTION_LINE_LIST,
    get_default_template,
)
from .services.legend_engine import compile_legend, build_prompt_block
from .services.legend_validator import validate_tags as validate_tags_against_legend
from .services.line_list_parser import parse_line_list, ParseError as LineListParseError
from .services.line_list_cross_check import cross_check as cross_check_tags
from .services.equipment_list_parser import parse_equipment_list, ParseError as EquipmentListParseError
from .services.equipment_cross_check import cross_check as cross_check_equipment_tags
from .services.instrument_index_parser import parse_instrument_index, ParseError as InstrumentIndexParseError
from .services.instrument_cross_check import cross_check as cross_check_instrument_tags
from .services.line_tag_extractor import extract_line_tags, summarize
from .services.vision_extractor import (
    extract_line_tags_via_vision,
    SUPPORTED_PROVIDERS,
)

logger = logging.getLogger(__name__)


# ─── Soft-coded config ────────────────────────────────────────────────
UPLOAD_FIELD_NAME = 'file'
MAX_UPLOAD_MB = 25
ALLOWED_EXTENSIONS = ('.pdf',)
ALLOWED_LINE_LIST_EXTENSIONS = ('.xlsx', '.xlsm')
MAX_LINE_LIST_MB = 15
ALLOWED_EQUIPMENT_LIST_EXTENSIONS = ('.xlsx', '.xlsm')
MAX_EQUIPMENT_LIST_MB = 15
ALLOWED_INSTRUMENT_INDEX_EXTENSIONS = ('.xlsx', '.xlsm')
MAX_INSTRUMENT_INDEX_MB = 15
SUPPORTED_MODES = (MODE_OCR, MODE_VISION)
HISTORY_PAGE_SIZE = 50   # max rows returned by the history list endpoint


def _summarize_from_dicts(tag_dicts: list[dict]) -> dict:
    by_group: dict[str, list[dict]] = {}
    for t in tag_dicts:
        by_group.setdefault(t.get('service_group') or t.get('service') or 'Other', []).append(t)
    return {
        'total': len(tag_dicts),
        'by_service_group': {g: len(items) for g, items in by_group.items()},
    }


def _persist_extraction(*, user, upload, pdf_bytes, mode, provider, model_name,
                        force_ocr, tag_dicts, summary) -> PidCheckerV2Extraction:
    """Atomically save the extraction + its line-tag rows."""
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    with transaction.atomic():
        extraction = PidCheckerV2Extraction.objects.create(
            created_by=user,
            filename=upload.name or '',
            file_size_bytes=upload.size or 0,
            file_sha256=sha,
            mode=mode,
            provider=provider or '',
            model=model_name or '',
            force_ocr=bool(force_ocr),
            tag_count=len(tag_dicts),
            summary_json=summary or {},
        )
        # Bulk-insert tags, deduping by tag string (unique constraint safety).
        seen: set[str] = set()
        rows: list[PidCheckerV2LineTag] = []
        for t in tag_dicts:
            key = t.get('tag') or ''
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(PidCheckerV2LineTag(
                extraction=extraction,
                tag=key,
                size=t.get('size') or '',
                service=t.get('service') or '',
                spec=t.get('spec') or '',
                serial=t.get('serial') or '',
                service_group=t.get('service_group') or '',
            ))
        if rows:
            PidCheckerV2LineTag.objects.bulk_create(rows, batch_size=500)
    return extraction


def _persist_token_usage(*, user, feature: str, usage: dict,
                         related_extraction_id=None, related_upload_id=None) -> None:
    """Persist a `UsageMeter.summary()` payload as one row per (provider,model)."""
    if not usage or not isinstance(usage, dict):
        return
    by_model = usage.get('by_model') or []
    if not by_model:
        return
    from decimal import Decimal
    try:
        rows = []
        for item in by_model:
            try:
                cost = Decimal(str(item.get('cost_usd') or '0'))
            except Exception:
                cost = Decimal('0')
            in_t = int(item.get('input_tokens') or 0)
            out_t = int(item.get('output_tokens') or 0)
            rows.append(PidCheckerV2UsageLog(
                created_by=user,
                feature=feature or '',
                provider=str(item.get('provider') or ''),
                model_name=str(item.get('model') or ''),
                call_count=int(item.get('calls') or 0),
                input_tokens=in_t,
                output_tokens=out_t,
                total_tokens=in_t + out_t,
                cost_usd=cost,
                related_extraction_id=related_extraction_id,
                related_upload_id=related_upload_id,
                notes={},
            ))
        if rows:
            PidCheckerV2UsageLog.objects.bulk_create(rows)
    except Exception:
        logger.exception('Failed to persist token usage for feature=%s', feature)


def _resolve_active_legend(user, section: str) -> PidCheckerV2LegendSheet | None:
    """Return the user's active legend for a section, or None."""
    return (
        PidCheckerV2LegendSheet.objects
        .filter(created_by=user, section=section, is_active=True)
        .first()
    )


# Sources reported to the client so the UI can show which legend is in use.
LEGEND_SOURCE_EXPLICIT = 'explicit'      # legend_id from client
LEGEND_SOURCE_ACTIVE = 'active'          # user's activated legend
LEGEND_SOURCE_LATEST = 'latest'          # most-recent legend (no active)
LEGEND_SOURCE_DEFAULT = 'default'        # built-in default template


def _resolve_legend_smart(user, section: str, legend_id=None):
    """Smart-fallback legend resolver used by validation.

    Returns a tuple: (legend_obj_or_None, definition_dict, name, source).

    Priority:
      1. Explicit legend_id
      2. User's active legend for the section
      3. User's most-recently updated legend for the section
      4. Built-in default template (compiled on the fly — no DB row)

    This means the validate endpoint always has something to compare
    against and never blocks the user with "activate one first".
    """
    if legend_id:
        obj = (
            PidCheckerV2LegendSheet.objects
            .filter(created_by=user, legend_id=legend_id)
            .first()
        )
        if obj:
            return obj, obj.definition, obj.name, LEGEND_SOURCE_EXPLICIT

    obj = _resolve_active_legend(user, section)
    if obj:
        return obj, obj.definition, obj.name, LEGEND_SOURCE_ACTIVE

    obj = (
        PidCheckerV2LegendSheet.objects
        .filter(created_by=user, section=section)
        .order_by('-updated_at')
        .first()
    )
    if obj:
        return obj, obj.definition, obj.name, LEGEND_SOURCE_LATEST

    # Final fallback: built-in default template (no persistence)
    if section in LEGEND_SECTIONS:
        tpl = get_default_template(section)
        return None, tpl['definition'], f"{tpl['name']} (built-in)", LEGEND_SOURCE_DEFAULT

    return None, None, '', ''


def _enrich_tags_with_legend(tag_dicts: list[dict], legend: PidCheckerV2LegendSheet | None) -> list[dict]:
    """Re-validate tags with the compiled legend regex and add lookup labels."""
    if legend is None:
        return tag_dicts
    try:
        compiled = compile_legend(legend.definition)
    except Exception:
        logger.exception('Legend compile failed at enrichment time')
        return tag_dicts
    out: list[dict] = []
    for t in tag_dicts:
        text = (t.get('tag') or '').strip()
        matched = compiled.match(text)
        if matched:
            merged = {**t, **matched}
            out.append(merged)
        else:
            # keep tag but flag as unmatched by the active legend
            out.append({**t, 'legend_match': False})
    # keep only tags that match the legend if any legend field was captured
    matched_only = [t for t in out if t.get('legend_match') is not False]
    return matched_only or out


class ExtractLineTagsView(APIView):
    """POST a PDF, receive extracted line tags — and auto-save the run."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        upload = request.FILES.get(UPLOAD_FIELD_NAME)
        if upload is None:
            return Response({'error': f"missing file field '{UPLOAD_FIELD_NAME}'"},
                            status=status.HTTP_400_BAD_REQUEST)
        if upload.size > MAX_UPLOAD_MB * 1024 * 1024:
            return Response({'error': f'file exceeds {MAX_UPLOAD_MB} MB limit'},
                            status=status.HTTP_400_BAD_REQUEST)
        name_lower = (upload.name or '').lower()
        if not name_lower.endswith(ALLOWED_EXTENSIONS):
            return Response({'error': f'only {ALLOWED_EXTENSIONS} allowed'},
                            status=status.HTTP_400_BAD_REQUEST)

        mode = (request.data.get('mode') or MODE_OCR).lower()
        if mode not in SUPPORTED_MODES:
            return Response({'error': f"mode must be one of {SUPPORTED_MODES}"},
                            status=status.HTTP_400_BAD_REQUEST)

        pdf_bytes = upload.read()
        legend = _resolve_active_legend(request.user, SECTION_LINE_LIST)
        legend_prompt = None
        if legend is not None:
            try:
                legend_prompt = build_prompt_block(compile_legend(legend.definition))
            except Exception:
                logger.exception('Failed to compile active legend — falling back to defaults')
                legend = None

        # ── Vision path ────────────────────────────────────────────
        if mode == MODE_VISION:
            provider = (request.data.get('provider') or '').lower()
            api_key = (request.data.get('api_key') or '').strip()
            if provider not in SUPPORTED_PROVIDERS:
                return Response({'error': f"provider must be one of {SUPPORTED_PROVIDERS}"},
                                status=status.HTTP_400_BAD_REQUEST)
            if not api_key:
                return Response({'error': 'api_key required for vision mode'},
                                status=status.HTTP_400_BAD_REQUEST)
            try:
                result = extract_line_tags_via_vision(
                    pdf_bytes, provider, api_key,
                    legend_prompt=legend_prompt,
                )
            except Exception as exc:
                logger.exception('Vision extraction failed')
                return Response({'error': f'vision extraction failed: {exc}'},
                                status=status.HTTP_502_BAD_GATEWAY)

            tag_dicts = _enrich_tags_with_legend(result['tags'], legend)
            summary = _summarize_from_dicts(tag_dicts)
            try:
                extraction = _persist_extraction(
                    user=request.user, upload=upload, pdf_bytes=pdf_bytes,
                    mode=MODE_VISION, provider=result['provider'],
                    model_name=result['model'], force_ocr=False,
                    tag_dicts=tag_dicts, summary=summary,
                )
            except Exception:
                logger.exception('Auto-save of vision extraction failed')
                extraction = None

            _persist_token_usage(
                user=request.user,
                feature='line_extraction',
                usage=result.get('token_usage') or {},
                related_extraction_id=extraction.extraction_id if extraction else None,
            )

            return Response({
                'extraction_id': str(extraction.extraction_id) if extraction else None,
                'filename': upload.name,
                'mode': MODE_VISION,
                'provider': result['provider'],
                'model': result['model'],
                'tags': tag_dicts,
                'summary': summary,
                'legend_id': str(legend.legend_id) if legend else None,
                'legend_name': legend.name if legend else None,
                'created_at': extraction.created_at.isoformat() if extraction else None,
                'token_usage': result.get('token_usage'),
            }, status=status.HTTP_200_OK)

        # ── OCR path ───────────────────────────────────────────────
        force_ocr = str(request.data.get('force_ocr', '')).lower() in ('1', 'true', 'yes')
        tags = extract_line_tags(pdf_bytes, force_ocr=force_ocr)
        tag_dicts = _enrich_tags_with_legend([t.to_dict() for t in tags], legend)
        summary = summarize(tags) if legend is None else _summarize_from_dicts(tag_dicts)
        try:
            extraction = _persist_extraction(
                user=request.user, upload=upload, pdf_bytes=pdf_bytes,
                mode=MODE_OCR, provider='', model_name='',
                force_ocr=force_ocr, tag_dicts=tag_dicts, summary=summary,
            )
        except Exception:
            logger.exception('Auto-save of OCR extraction failed')
            extraction = None

        return Response({
            'extraction_id': str(extraction.extraction_id) if extraction else None,
            'filename': upload.name,
            'mode': MODE_OCR,
            'tags': tag_dicts,
            'summary': summary,
            'legend_id': str(legend.legend_id) if legend else None,
            'legend_name': legend.name if legend else None,
            'created_at': extraction.created_at.isoformat() if extraction else None,
        }, status=status.HTTP_200_OK)


# ─── History endpoints ────────────────────────────────────────────────
class ExtractionListView(ListAPIView):
    """List the current user's extraction runs (most recent first)."""

    permission_classes = [IsAuthenticated]
    serializer_class = PidCheckerV2ExtractionListSerializer
    pagination_class = None

    def get_queryset(self):
        return (
            PidCheckerV2Extraction.objects
            .filter(created_by=self.request.user)
            .order_by('-created_at')[:HISTORY_PAGE_SIZE]
        )


class ExtractionDetailView(RetrieveAPIView):
    """Retrieve one extraction (with tags) or delete it."""

    permission_classes = [IsAuthenticated]
    serializer_class = PidCheckerV2ExtractionDetailSerializer
    lookup_field = 'extraction_id'

    def get_queryset(self):
        return PidCheckerV2Extraction.objects.filter(created_by=self.request.user)

    def delete(self, request, extraction_id, *args, **kwargs):
        obj = get_object_or_404(
            PidCheckerV2Extraction,
            extraction_id=extraction_id,
            created_by=request.user,
        )
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Legend Sheet endpoints ───────────────────────────────────────────
class LegendSheetListCreateView(APIView):
    """GET → list current user's legends (optionally filter by ?section=).
       POST → create a legend."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = PidCheckerV2LegendSheet.objects.filter(created_by=request.user)
        section = request.query_params.get('section')
        if section:
            qs = qs.filter(section=section)
        qs = qs.order_by('-updated_at')
        return Response(
            PidCheckerV2LegendSheetSerializer(qs, many=True).data,
            status=status.HTTP_200_OK,
        )

    def post(self, request):
        serializer = PidCheckerV2LegendSheetSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        obj = serializer.save(created_by=request.user)
        return Response(
            PidCheckerV2LegendSheetSerializer(obj).data,
            status=status.HTTP_201_CREATED,
        )


class LegendSheetDetailView(APIView):
    """GET / PATCH / DELETE one legend."""

    permission_classes = [IsAuthenticated]

    def _get_obj(self, request, legend_id):
        return get_object_or_404(
            PidCheckerV2LegendSheet,
            legend_id=legend_id,
            created_by=request.user,
        )

    def get(self, request, legend_id):
        obj = self._get_obj(request, legend_id)
        return Response(PidCheckerV2LegendSheetSerializer(obj).data)

    def patch(self, request, legend_id):
        obj = self._get_obj(request, legend_id)
        serializer = PidCheckerV2LegendSheetSerializer(obj, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)

    def delete(self, request, legend_id):
        obj = self._get_obj(request, legend_id)
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LegendSheetActivateView(APIView):
    """POST → deactivate all sibling legends and activate this one."""

    permission_classes = [IsAuthenticated]

    def post(self, request, legend_id):
        obj = get_object_or_404(
            PidCheckerV2LegendSheet,
            legend_id=legend_id,
            created_by=request.user,
        )
        with transaction.atomic():
            (PidCheckerV2LegendSheet.objects
                .filter(created_by=request.user, section=obj.section)
                .exclude(pk=obj.pk)
                .update(is_active=False))
            obj.is_active = True
            obj.save(update_fields=['is_active', 'updated_at'])
        return Response(PidCheckerV2LegendSheetSerializer(obj).data)


class LegendSheetDefaultTemplateView(APIView):
    """GET → return the built-in default template for a section (?section=line_list)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        section = request.query_params.get('section') or ''
        if section not in LEGEND_SECTIONS:
            return Response(
                {'error': f'unknown section — choose one of {list(LEGEND_SECTIONS)}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        template = get_default_template(section)
        return Response({
            'section': section,
            'name': template['name'],
            'description': template['description'],
            'definition': template['definition'],
        })


class ValidateLineTagsView(APIView):
    """POST tags + active legend → return per-tag findings with optional AI diagnosis.

    Body:
        tags:         list[dict]  (each dict must have a 'tag' field)
        section:      str         default 'line_list'
        legend_id:    UUID        optional — override active legend
        use_ai:       bool        default False
        vision_provider: 'openai'|'claude'   (required if use_ai=True)
        vision_api_key: str                   (required if use_ai=True)
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tags = request.data.get('tags')
        if not isinstance(tags, list):
            return Response({'error': 'tags must be a list'}, status=status.HTTP_400_BAD_REQUEST)

        section = request.data.get('section') or SECTION_LINE_LIST
        legend_id = request.data.get('legend_id')
        legend_obj, definition, legend_name, legend_source = _resolve_legend_smart(
            request.user, section, legend_id=legend_id,
        )
        if definition is None:
            return Response(
                {'error': f'No legend and no default template available for section {section!r}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            compiled = compile_legend(definition)
        except Exception as exc:
            return Response({'error': f'Legend is invalid: {exc}'}, status=status.HTTP_400_BAD_REQUEST)

        use_ai = bool(request.data.get('use_ai'))
        ai_provider = (request.data.get('vision_provider') or '').lower() or None
        ai_api_key = request.data.get('vision_api_key') or None
        if use_ai:
            if ai_provider not in SUPPORTED_PROVIDERS or not ai_api_key:
                return Response(
                    {'error': 'use_ai=true requires vision_provider (openai|claude) and vision_api_key'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            result = validate_tags_against_legend(
                tags,
                compiled,
                use_ai=use_ai,
                ai_provider=ai_provider,
                ai_api_key=ai_api_key,
            )
        except Exception as exc:
            logger.exception('Validation failed')
            return Response({'error': f'validation failed: {exc}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({
            'section': section,
            'legend_id': str(legend_obj.legend_id) if legend_obj else None,
            'legend_name': legend_name,
            'legend_source': legend_source,
            **result,
        })


# ═════════════════════════════════════════════════════════════════════
# Master Line List (Excel) upload + cross-check
# ═════════════════════════════════════════════════════════════════════

def _resolve_active_line_list(user):
    return (
        PidCheckerV2LineListUpload.objects
        .filter(created_by=user, is_active=True)
        .first()
    )


def _persist_line_list(user, filename: str, parsed: dict) -> PidCheckerV2LineListUpload:
    """Save parsed rows and mark this upload as the active one."""
    meta = parsed.get('meta') or {}
    with transaction.atomic():
        # Deactivate any previously-active line list for this user
        PidCheckerV2LineListUpload.objects.filter(
            created_by=user, is_active=True,
        ).update(is_active=False)

        upload = PidCheckerV2LineListUpload.objects.create(
            created_by=user,
            filename=filename[:300],
            sheet_name=(meta.get('sheet_name') or '')[:200],
            title=(meta.get('title') or '')[:500],
            doc_no=(meta.get('doc_no') or '')[:500],
            doc_date=(meta.get('date') or '')[:64],
            pid_extract_ref=(meta.get('pid_extract_ref') or '')[:500],
            total_rows=len(parsed.get('rows') or []),
            columns=parsed.get('columns') or {},
            summary=parsed.get('summary') or {},
            is_active=True,
        )

        rows = []
        KNOWN = {'excel_row', 'tag', 'size', 'service_code', 'serial', 'spec',
                 'from', 'to', 'pid_no', 'fluid_service'}
        for r in (parsed.get('rows') or []):
            extras = {k: v for k, v in r.items() if k not in KNOWN and not k.startswith('_')}
            rows.append(PidCheckerV2LineListRow(
                upload=upload,
                excel_row=r.get('_excel_row') or 0,
                tag=(r.get('tag') or '')[:200],
                size=str(r.get('size') or '')[:32],
                service_code=str(r.get('service_code') or '')[:16],
                serial=str(r.get('serial') or '')[:32],
                spec=str(r.get('spec') or '')[:32],
                from_ref=(r.get('from') or '')[:300],
                to_ref=(r.get('to') or '')[:300],
                pid_no=(r.get('pid_no') or '')[:300],
                fluid_service=(r.get('fluid_service') or '')[:200],
                extras=extras,
            ))
        if rows:
            PidCheckerV2LineListRow.objects.bulk_create(rows, batch_size=500)
    return upload


class LineListUploadView(APIView):
    """GET → list user's uploads.  POST → parse + save an xlsx."""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        qs = (
            PidCheckerV2LineListUpload.objects
            .filter(created_by=request.user)
            .order_by('-created_at')[:HISTORY_PAGE_SIZE]
        )
        return Response(PidCheckerV2LineListListSerializer(qs, many=True).data)

    def post(self, request):
        upload = request.FILES.get(UPLOAD_FIELD_NAME)
        if upload is None:
            return Response({'error': f"missing file field '{UPLOAD_FIELD_NAME}'"},
                            status=status.HTTP_400_BAD_REQUEST)
        if upload.size > MAX_LINE_LIST_MB * 1024 * 1024:
            return Response({'error': f'file exceeds {MAX_LINE_LIST_MB} MB limit'},
                            status=status.HTTP_400_BAD_REQUEST)
        name_lower = (upload.name or '').lower()
        if not name_lower.endswith(ALLOWED_LINE_LIST_EXTENSIONS):
            return Response({'error': f'only {ALLOWED_LINE_LIST_EXTENSIONS} allowed'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            parsed = parse_line_list(upload.read(), upload.name)
        except LineListParseError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception('Line list parse crashed')
            return Response({'error': f'parse failed: {exc}'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        obj = _persist_line_list(request.user, upload.name, parsed)
        return Response(
            PidCheckerV2LineListDetailSerializer(obj).data,
            status=status.HTTP_201_CREATED,
        )


class LineListListView(APIView):
    """Deprecated — kept for backward compat; use LineListUploadView.get."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (
            PidCheckerV2LineListUpload.objects
            .filter(created_by=request.user)
            .order_by('-created_at')[:HISTORY_PAGE_SIZE]
        )
        return Response(PidCheckerV2LineListListSerializer(qs, many=True).data)


class LineListDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, line_list_id):
        obj = get_object_or_404(
            PidCheckerV2LineListUpload,
            created_by=request.user, line_list_id=line_list_id,
        )
        return Response(PidCheckerV2LineListDetailSerializer(obj).data)

    def delete(self, request, line_list_id):
        obj = get_object_or_404(
            PidCheckerV2LineListUpload,
            created_by=request.user, line_list_id=line_list_id,
        )
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class LineListActivateView(APIView):
    """POST → mark this line list active for the user (deactivate siblings)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, line_list_id):
        obj = get_object_or_404(
            PidCheckerV2LineListUpload,
            created_by=request.user, line_list_id=line_list_id,
        )
        with transaction.atomic():
            PidCheckerV2LineListUpload.objects.filter(
                created_by=request.user, is_active=True,
            ).exclude(pk=obj.pk).update(is_active=False)
            if not obj.is_active:
                obj.is_active = True
                obj.save(update_fields=['is_active', 'updated_at'])
        return Response(PidCheckerV2LineListDetailSerializer(obj).data)


class CrossCheckView(APIView):
    """POST P&ID tags → compare against user's active (or specified) Line List.

    Body:
        tags:           list[dict]  each with at least 'tag' (composite)
        line_list_id:   UUID        optional — override active line list
        use_ai:         bool        default False
        vision_provider, vision_api_key   (required if use_ai=True)
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tags = request.data.get('tags')
        if not isinstance(tags, list):
            return Response({'error': 'tags must be a list'},
                            status=status.HTTP_400_BAD_REQUEST)

        line_list_id = request.data.get('line_list_id')
        if line_list_id:
            line_list = (
                PidCheckerV2LineListUpload.objects
                .filter(created_by=request.user, line_list_id=line_list_id)
                .first()
            )
        else:
            line_list = _resolve_active_line_list(request.user)
        if line_list is None:
            return Response(
                {'error': 'No master Line List uploaded yet. Upload one first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ll_rows = [
            {
                'tag': r.tag,
                'size': r.size,
                'service_code': r.service_code,
                'serial': r.serial,
                'spec': r.spec,
                'from_ref': r.from_ref,
                'to_ref': r.to_ref,
                'fluid_service': r.fluid_service,
                'excel_row': r.excel_row,
            }
            for r in line_list.rows.all()
        ]

        use_ai = bool(request.data.get('use_ai'))
        ai_provider = (request.data.get('vision_provider') or '').lower() or None
        ai_api_key = request.data.get('vision_api_key') or None
        if use_ai and (ai_provider not in SUPPORTED_PROVIDERS or not ai_api_key):
            return Response(
                {'error': 'use_ai=true requires vision_provider (openai|claude) and vision_api_key'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = cross_check_tags(
                tags, ll_rows,
                use_ai=use_ai,
                ai_provider=ai_provider,
                ai_api_key=ai_api_key,
            )
        except Exception as exc:
            logger.exception('Cross-check failed')
            return Response({'error': f'cross-check failed: {exc}'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        _persist_token_usage(
            user=request.user,
            feature='line_list_cross_check',
            usage=result.get('token_usage') or {},
            related_upload_id=line_list.line_list_id,
        )

        return Response({
            'line_list_id': str(line_list.line_list_id),
            'line_list_filename': line_list.filename,
            'line_list_title': line_list.title,
            **result,
        })


# ═════════════════════════════════════════════════════════════════════
# Master Equipment List (Excel) upload + cross-check
# ═════════════════════════════════════════════════════════════════════

def _resolve_active_equipment_list(user):
    return (
        PidCheckerV2EquipmentListUpload.objects
        .filter(created_by=user, is_active=True)
        .first()
    )


EQUIPMENT_LIST_KNOWN_FIELDS = {
    'excel_row', 'tag', 'description', 'design_flow', 'op_pressure', 'op_temp',
    'design_p_min', 'design_p_max', 'design_t_min', 'design_t_max',
    'moc', 'insulation', 'dim_length', 'dim_diameter', 'motor_rating',
    'pid_no', 'qty', 'phase', 'remarks', 'sno', 'rev',
}


def _persist_equipment_list(user, filename: str, parsed: dict) -> PidCheckerV2EquipmentListUpload:
    """Save parsed rows and mark this upload as the active one."""
    meta = parsed.get('meta') or {}
    with transaction.atomic():
        PidCheckerV2EquipmentListUpload.objects.filter(
            created_by=user, is_active=True,
        ).update(is_active=False)

        upload = PidCheckerV2EquipmentListUpload.objects.create(
            created_by=user,
            filename=filename[:300],
            sheet_name=(meta.get('sheet_name') or '')[:200],
            title=(meta.get('title') or '')[:500],
            doc_no=(meta.get('doc_no') or '')[:500],
            doc_date=(meta.get('date') or '')[:64],
            pid_extract_ref=(meta.get('pid_extract_ref') or '')[:500],
            company=(meta.get('company') or '')[:500],
            project=(meta.get('project') or '')[:500],
            total_rows=len(parsed.get('rows') or []),
            columns=parsed.get('columns') or {},
            summary=parsed.get('summary') or {},
            is_active=True,
        )

        rows = []
        for r in (parsed.get('rows') or []):
            extras = {k: v for k, v in r.items() if k not in EQUIPMENT_LIST_KNOWN_FIELDS and not k.startswith('_')}
            rows.append(PidCheckerV2EquipmentListRow(
                upload=upload,
                excel_row=r.get('_excel_row') or 0,
                tag=(r.get('tag') or '')[:64],
                description=str(r.get('description') or '')[:300],
                design_flow=str(r.get('design_flow') or '')[:100],
                op_pressure=str(r.get('op_pressure') or '')[:64],
                op_temp=str(r.get('op_temp') or '')[:64],
                design_p_min=str(r.get('design_p_min') or '')[:32],
                design_p_max=str(r.get('design_p_max') or '')[:32],
                design_t_min=str(r.get('design_t_min') or '')[:32],
                design_t_max=str(r.get('design_t_max') or '')[:32],
                moc=str(r.get('moc') or '')[:100],
                insulation=str(r.get('insulation') or '')[:64],
                dim_length=str(r.get('dim_length') or '')[:32],
                dim_diameter=str(r.get('dim_diameter') or '')[:32],
                motor_rating=str(r.get('motor_rating') or '')[:32],
                pid_no=str(r.get('pid_no') or '')[:300],
                qty=str(r.get('qty') or '')[:16],
                phase=str(r.get('phase') or '')[:64],
                remarks=str(r.get('remarks') or '')[:500],
                extras=extras,
            ))
        if rows:
            PidCheckerV2EquipmentListRow.objects.bulk_create(rows, batch_size=500)
    return upload


class EquipmentListUploadView(APIView):
    """GET → list user's uploads.  POST → parse + save an xlsx."""
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        qs = (
            PidCheckerV2EquipmentListUpload.objects
            .filter(created_by=request.user)
            .order_by('-created_at')[:HISTORY_PAGE_SIZE]
        )
        return Response(PidCheckerV2EquipmentListListSerializer(qs, many=True).data)

    def post(self, request):
        upload = request.FILES.get(UPLOAD_FIELD_NAME)
        if upload is None:
            return Response({'error': f"missing file field '{UPLOAD_FIELD_NAME}'"},
                            status=status.HTTP_400_BAD_REQUEST)
        if upload.size > MAX_EQUIPMENT_LIST_MB * 1024 * 1024:
            return Response({'error': f'file exceeds {MAX_EQUIPMENT_LIST_MB} MB limit'},
                            status=status.HTTP_400_BAD_REQUEST)
        name_lower = (upload.name or '').lower()
        if not name_lower.endswith(ALLOWED_EQUIPMENT_LIST_EXTENSIONS):
            return Response({'error': f'only {ALLOWED_EQUIPMENT_LIST_EXTENSIONS} allowed'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            parsed = parse_equipment_list(upload.read(), upload.name)
        except EquipmentListParseError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception('Equipment list parse crashed')
            return Response({'error': f'parse failed: {exc}'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        obj = _persist_equipment_list(request.user, upload.name, parsed)
        return Response(
            PidCheckerV2EquipmentListDetailSerializer(obj).data,
            status=status.HTTP_201_CREATED,
        )


class EquipmentListDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, equipment_list_id):
        obj = get_object_or_404(
            PidCheckerV2EquipmentListUpload,
            created_by=request.user, equipment_list_id=equipment_list_id,
        )
        return Response(PidCheckerV2EquipmentListDetailSerializer(obj).data)

    def delete(self, request, equipment_list_id):
        obj = get_object_or_404(
            PidCheckerV2EquipmentListUpload,
            created_by=request.user, equipment_list_id=equipment_list_id,
        )
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EquipmentListActivateView(APIView):
    """POST → mark this equipment list active for the user (deactivate siblings)."""
    permission_classes = [IsAuthenticated]

    def post(self, request, equipment_list_id):
        obj = get_object_or_404(
            PidCheckerV2EquipmentListUpload,
            created_by=request.user, equipment_list_id=equipment_list_id,
        )
        with transaction.atomic():
            PidCheckerV2EquipmentListUpload.objects.filter(
                created_by=request.user, is_active=True,
            ).exclude(pk=obj.pk).update(is_active=False)
            if not obj.is_active:
                obj.is_active = True
                obj.save(update_fields=['is_active', 'updated_at'])
        return Response(PidCheckerV2EquipmentListDetailSerializer(obj).data)


class EquipmentCrossCheckView(APIView):
    """POST equipment tags → compare against user's active (or specified) Equipment List.

    Body:
        equipment_tags:       list[str]   tags read from the P&ID
        equipment_list_id:    UUID        optional — override active list
        use_ai:               bool        default False
        vision_provider, vision_api_key   (required if use_ai=True OR when
                                            equipment_attributes is present)
        equipment_attributes: dict[str, dict[str,str]]
                                          optional — per-tag attribute map
                                          from Vision extractor; triggers
                                          attribute-level comparison.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tags = request.data.get('equipment_tags')
        if not isinstance(tags, list):
            return Response({'error': 'equipment_tags must be a list of strings'},
                            status=status.HTTP_400_BAD_REQUEST)

        equipment_list_id = request.data.get('equipment_list_id')
        if equipment_list_id:
            equipment_list = (
                PidCheckerV2EquipmentListUpload.objects
                .filter(created_by=request.user, equipment_list_id=equipment_list_id)
                .first()
            )
        else:
            equipment_list = _resolve_active_equipment_list(request.user)
        if equipment_list is None:
            return Response(
                {'error': 'No master Equipment List uploaded yet. Upload one first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        el_rows = [
            {
                'tag': r.tag,
                'description': r.description,
                'pid_no': r.pid_no,
                'moc': r.moc,
                'phase': r.phase,
                'excel_row': r.excel_row,
                'nominal_capacity':    r.nominal_capacity,
                'length_tt':           r.length_tt,
                'diameter_id':         r.diameter_id,
                'op_pressure':         r.op_pressure,
                'design_p_min':        r.design_p_min,
                'design_p_max':        r.design_p_max,
                'op_temp':             r.op_temp,
                'design_t_min':        r.design_t_min,
                'design_t_max':        r.design_t_max,
                'material_shell':      r.material_shell,
                'material_internal':   r.material_internal,
                'trim':                r.trim,
            }
            for r in equipment_list.rows.all()
        ]

        use_ai = bool(request.data.get('use_ai'))
        ai_provider = (request.data.get('vision_provider') or '').lower() or None
        ai_api_key = request.data.get('vision_api_key') or None
        equipment_attributes = request.data.get('equipment_attributes') or None
        if equipment_attributes is not None and not isinstance(equipment_attributes, dict):
            return Response(
                {'error': 'equipment_attributes must be an object mapping tag → {attribute_key: value}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if use_ai and (ai_provider not in SUPPORTED_PROVIDERS or not ai_api_key):
            return Response(
                {'error': 'use_ai=true requires vision_provider (openai|claude) and vision_api_key'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if equipment_attributes and (ai_provider not in SUPPORTED_PROVIDERS or not ai_api_key):
            return Response(
                {'error': 'equipment_attributes require vision_provider (openai|claude) and vision_api_key',
                 'code': 'byok_required_for_attributes'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = cross_check_equipment_tags(
                tags, el_rows,
                use_ai=use_ai,
                ai_provider=ai_provider,
                ai_api_key=ai_api_key,
                pid_attributes=equipment_attributes,
            )
        except Exception as exc:
            logger.exception('Equipment cross-check failed')
            return Response({'error': f'cross-check failed: {exc}'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        _persist_token_usage(
            user=request.user,
            feature='equipment_cross_check',
            usage=result.get('token_usage') or {},
            related_upload_id=equipment_list.equipment_list_id,
        )

        return Response({
            'equipment_list_id': str(equipment_list.equipment_list_id),
            'equipment_list_filename': equipment_list.filename,
            'equipment_list_title': equipment_list.title,
            **result,
        })


# ---------------------------------------------------------------------
# Instrument Index — upload / list / detail / activate / cross-check
# ---------------------------------------------------------------------

def _resolve_active_instrument_index(user):
    return (
        PidCheckerV2InstrumentIndexUpload.objects
        .filter(created_by=user, is_active=True)
        .first()
    )


INSTRUMENT_INDEX_KNOWN_FIELDS = {
    'tag', 'instrument_type', 'service_description', 'pid_no', 'line_no',
    'eqpt_no', 'location', 'ex_class', 'power_supply',
    'range_min', 'range_max', 'range_unit',
    'cal_min', 'cal_max', 'cal_unit',
    'datasheet_no', 'loop_dwg_no', 'hookup_dwg_no', 'location_layout_no',
    'manufacturer', 'model', 'remarks', 'rev',
}


def _persist_instrument_index(user, filename: str, parsed: dict) -> PidCheckerV2InstrumentIndexUpload:
    meta = parsed.get('meta') or {}
    summary = parsed.get('summary') or {}
    with transaction.atomic():
        PidCheckerV2InstrumentIndexUpload.objects.filter(
            created_by=user, is_active=True
        ).update(is_active=False)

        upload = PidCheckerV2InstrumentIndexUpload.objects.create(
            created_by=user,
            filename=filename[:300],
            sheet_name=(meta.get('sheet_name') or '')[:200],
            title=(meta.get('title') or '')[:500],
            doc_no=(meta.get('doc_no') or '')[:500],
            doc_date=(meta.get('date') or '')[:64],
            pid_extract_ref=(meta.get('pid_extract_ref') or '')[:500],
            company=(meta.get('company') or '')[:300],
            project=(meta.get('project') or '')[:500],
            total_rows=summary.get('total') or 0,
            columns=parsed.get('columns') or {},
            summary=summary,
            is_active=True,
        )
        rows = []
        for r in parsed.get('rows') or []:
            extras = {
                k: v for k, v in r.items()
                if k not in INSTRUMENT_INDEX_KNOWN_FIELDS and not k.startswith('_')
            }
            rows.append(PidCheckerV2InstrumentIndexRow(
                upload=upload,
                excel_row=r.get('_excel_row') or 0,
                tag=str(r.get('tag') or '')[:64],
                instrument_type=str(r.get('instrument_type') or '')[:200],
                service_description=str(r.get('service_description') or '')[:500],
                pid_no=str(r.get('pid_no') or '')[:300],
                line_no=str(r.get('line_no') or '')[:200],
                eqpt_no=str(r.get('eqpt_no') or '')[:64],
                location=str(r.get('location') or '')[:64],
                ex_class=str(r.get('ex_class') or '')[:64],
                power_supply=str(r.get('power_supply') or '')[:64],
                range_min=str(r.get('range_min') or '')[:32],
                range_max=str(r.get('range_max') or '')[:32],
                range_unit=str(r.get('range_unit') or '')[:32],
                cal_min=str(r.get('cal_min') or '')[:32],
                cal_max=str(r.get('cal_max') or '')[:32],
                cal_unit=str(r.get('cal_unit') or '')[:32],
                datasheet_no=str(r.get('datasheet_no') or '')[:200],
                loop_dwg_no=str(r.get('loop_dwg_no') or '')[:200],
                hookup_dwg_no=str(r.get('hookup_dwg_no') or '')[:200],
                location_layout_no=str(r.get('location_layout_no') or '')[:200],
                manufacturer=str(r.get('manufacturer') or '')[:200],
                model=str(r.get('model') or '')[:200],
                remarks=str(r.get('remarks') or '')[:500],
                rev=str(r.get('rev') or '')[:16],
                extras=extras,
            ))
        if rows:
            PidCheckerV2InstrumentIndexRow.objects.bulk_create(rows, batch_size=500)
    return upload


class InstrumentIndexUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def get(self, request):
        qs = (
            PidCheckerV2InstrumentIndexUpload.objects
            .filter(created_by=request.user)
            .order_by('-is_active', '-created_at')
        )
        return Response(PidCheckerV2InstrumentIndexListSerializer(qs, many=True).data)

    def post(self, request):
        upload = request.FILES.get(UPLOAD_FIELD_NAME)
        if upload is None:
            return Response({'error': f'{UPLOAD_FIELD_NAME!r} field is required'},
                            status=status.HTTP_400_BAD_REQUEST)
        if upload.size > MAX_INSTRUMENT_INDEX_MB * 1024 * 1024:
            return Response({'error': f'file exceeds {MAX_INSTRUMENT_INDEX_MB} MB limit'},
                            status=status.HTTP_400_BAD_REQUEST)
        name_lower = upload.name.lower()
        if not name_lower.endswith(ALLOWED_INSTRUMENT_INDEX_EXTENSIONS):
            return Response({'error': f'only {ALLOWED_INSTRUMENT_INDEX_EXTENSIONS} allowed'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            parsed = parse_instrument_index(upload.read(), upload.name)
        except InstrumentIndexParseError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            logger.exception('Instrument Index parse failed')
            return Response({'error': f'parse failed: {exc}'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        obj = _persist_instrument_index(request.user, upload.name, parsed)
        return Response(
            PidCheckerV2InstrumentIndexDetailSerializer(obj).data,
            status=status.HTTP_201_CREATED,
        )


class InstrumentIndexDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, instrument_index_id):
        obj = get_object_or_404(
            PidCheckerV2InstrumentIndexUpload,
            created_by=request.user, instrument_index_id=instrument_index_id,
        )
        return Response(PidCheckerV2InstrumentIndexDetailSerializer(obj).data)

    def delete(self, request, instrument_index_id):
        obj = get_object_or_404(
            PidCheckerV2InstrumentIndexUpload,
            created_by=request.user, instrument_index_id=instrument_index_id,
        )
        obj.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class InstrumentIndexActivateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, instrument_index_id):
        obj = get_object_or_404(
            PidCheckerV2InstrumentIndexUpload,
            created_by=request.user, instrument_index_id=instrument_index_id,
        )
        with transaction.atomic():
            PidCheckerV2InstrumentIndexUpload.objects.filter(
                created_by=request.user, is_active=True
            ).update(is_active=False)
            obj.is_active = True
            obj.save(update_fields=['is_active', 'updated_at'])
        return Response(PidCheckerV2InstrumentIndexDetailSerializer(obj).data)


class InstrumentCrossCheckView(APIView):
    """POST instrument tags ? compare against user's active (or specified) Instrument Index.

    Body:
        instrument_tags:        list[str]
        instrument_index_id:    UUID        optional — override active
        use_ai:                 bool        default False
        vision_provider, vision_api_key    (required if use_ai=True OR when
                                             instrument_attributes is present)
        instrument_attributes:  dict[str, dict[str,str]]
                                             optional — per-tag attribute map
                                             from Vision extractor; triggers
                                             attribute-level comparison.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        tags = request.data.get('instrument_tags')
        if not isinstance(tags, list):
            return Response({'error': 'instrument_tags must be a list of strings'},
                            status=status.HTTP_400_BAD_REQUEST)

        instrument_index_id = request.data.get('instrument_index_id')
        if instrument_index_id:
            instrument_index = (
                PidCheckerV2InstrumentIndexUpload.objects
                .filter(created_by=request.user, instrument_index_id=instrument_index_id)
                .first()
            )
        else:
            instrument_index = _resolve_active_instrument_index(request.user)
        if instrument_index is None:
            return Response(
                {'error': 'No master Instrument Index uploaded yet. Upload one first.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ii_rows = [
            {
                'tag': r.tag,
                'instrument_type': r.instrument_type,
                'service_description': r.service_description,
                'pid_no': r.pid_no,
                'eqpt_no': r.eqpt_no,
                'line_no': r.line_no,
                'excel_row': r.excel_row,
                'range_min':    r.range_min,
                'range_max':    r.range_max,
                'range_unit':   r.range_unit,
                'cal_min':      r.cal_min,
                'cal_max':      r.cal_max,
                'cal_unit':     r.cal_unit,
                'ex_class':     r.ex_class,
                'power_supply': r.power_supply,
                'manufacturer': r.manufacturer,
                'model':        r.model,
            }
            for r in instrument_index.rows.all()
        ]

        use_ai = bool(request.data.get('use_ai'))
        ai_provider = (request.data.get('vision_provider') or '').lower() or None
        ai_api_key = request.data.get('vision_api_key') or None
        instrument_attributes = request.data.get('instrument_attributes') or None
        if instrument_attributes is not None and not isinstance(instrument_attributes, dict):
            return Response(
                {'error': 'instrument_attributes must be an object mapping tag → {attribute_key: value}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if use_ai and (ai_provider not in SUPPORTED_PROVIDERS or not ai_api_key):
            return Response(
                {'error': 'use_ai=true requires vision_provider (openai|claude) and vision_api_key'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if instrument_attributes and (ai_provider not in SUPPORTED_PROVIDERS or not ai_api_key):
            return Response(
                {'error': 'instrument_attributes require vision_provider (openai|claude) and vision_api_key',
                 'code': 'byok_required_for_attributes'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = cross_check_instrument_tags(
                tags, ii_rows,
                use_ai=use_ai,
                ai_provider=ai_provider,
                ai_api_key=ai_api_key,
                pid_attributes=instrument_attributes,
            )
        except Exception as exc:
            logger.exception('Instrument cross-check failed')
            return Response({'error': f'cross-check failed: {exc}'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        _persist_token_usage(
            user=request.user,
            feature='instrument_cross_check',
            usage=result.get('token_usage') or {},
            related_upload_id=instrument_index.instrument_index_id,
        )

        return Response({
            'instrument_index_id': str(instrument_index.instrument_index_id),
            'instrument_index_filename': instrument_index.filename,
            'instrument_index_title': instrument_index.title,
            **result,
        })


# ─── Vision-based tag extraction from P&ID (BYOK) ─────────────────────
# Dedicated equipment / instrument extractors used by the cross-check
# panels. They accept only the PDF + BYOK, run a multi-tile Vision pass
# targeted at the specific tag class, and return the tag list. No DB
# persistence — this is a stateless helper for the cross-check flow.
class ExtractEquipmentTagsFromPidView(APIView):
    """POST a P&ID PDF + BYOK, receive equipment tags found on the drawing."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        upload = request.FILES.get(UPLOAD_FIELD_NAME)
        if upload is None:
            return Response({'error': f"missing file field '{UPLOAD_FIELD_NAME}'"},
                            status=status.HTTP_400_BAD_REQUEST)
        if upload.size > MAX_UPLOAD_MB * 1024 * 1024:
            return Response({'error': f'file exceeds {MAX_UPLOAD_MB} MB limit'},
                            status=status.HTTP_400_BAD_REQUEST)
        name_lower = (upload.name or '').lower()
        if not name_lower.endswith(ALLOWED_EXTENSIONS):
            return Response({'error': f'only {ALLOWED_EXTENSIONS} allowed'},
                            status=status.HTTP_400_BAD_REQUEST)

        provider = (request.data.get('provider') or '').lower()
        api_key = (request.data.get('api_key') or '').strip()
        if provider not in SUPPORTED_PROVIDERS:
            return Response({'error': f'provider must be one of {SUPPORTED_PROVIDERS}'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not api_key:
            return Response({'error': 'api_key required for vision extraction'},
                            status=status.HTTP_400_BAD_REQUEST)

        from .services.equipment_vision_extractor import extract_equipment_tags_via_vision
        try:
            result = extract_equipment_tags_via_vision(upload.read(), provider, api_key)
        except Exception as exc:
            logger.exception('Equipment vision extraction failed')
            msg = str(exc)
            if 'overloaded' in msg.lower() or '529' in msg:
                friendly = (f"{provider.title()} vision API is temporarily overloaded "
                            "after several automatic retries. Please try again in a "
                            "minute or switch provider.")
            else:
                friendly = f'vision extraction failed: {exc}'
            return Response({'error': friendly},
                            status=status.HTTP_502_BAD_GATEWAY)

        _persist_token_usage(
            user=request.user,
            feature='equipment_extraction',
            usage=result.get('token_usage') or {},
        )

        return Response({
            'filename': upload.name,
            'provider': result['provider'],
            'model':    result['model'],
            'tags':     result['tags'],
            'call_count': result['call_count'],
            'token_usage': result.get('token_usage'),
        }, status=status.HTTP_200_OK)


class ExtractInstrumentTagsFromPidView(APIView):
    """POST a P&ID PDF + BYOK, dispatch async vision extraction.

    Returns HTTP 202 with a ``job_id`` and ``poll_url`` — the actual work
    runs in Celery (``run_extract_instrument_tags_task``) so long-running
    heavy P&IDs no longer hit Gunicorn's request timeout.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, *args, **kwargs):
        import base64 as _b64
        import threading as _threading
        import uuid as _uuid

        from django.core.cache import cache as _cache
        from .tasks import (
            run_extract_instrument_tags_task,
            EXTRACT_INSTRUMENT_CACHE_KEY_FMT,
            EXTRACT_TAG_RESULT_TTL_S,
        )

        upload = request.FILES.get(UPLOAD_FIELD_NAME)
        if upload is None:
            return Response({'error': f"missing file field '{UPLOAD_FIELD_NAME}'"},
                            status=status.HTTP_400_BAD_REQUEST)
        if upload.size > MAX_UPLOAD_MB * 1024 * 1024:
            return Response({'error': f'file exceeds {MAX_UPLOAD_MB} MB limit'},
                            status=status.HTTP_400_BAD_REQUEST)
        name_lower = (upload.name or '').lower()
        if not name_lower.endswith(ALLOWED_EXTENSIONS):
            return Response({'error': f'only {ALLOWED_EXTENSIONS} allowed'},
                            status=status.HTTP_400_BAD_REQUEST)

        provider = (request.data.get('provider') or '').lower()
        api_key = (request.data.get('api_key') or '').strip()
        if provider not in SUPPORTED_PROVIDERS:
            return Response({'error': f'provider must be one of {SUPPORTED_PROVIDERS}'},
                            status=status.HTTP_400_BAD_REQUEST)
        if not api_key:
            return Response({'error': 'api_key required for vision extraction'},
                            status=status.HTTP_400_BAD_REQUEST)

        job_id = _uuid.uuid4().hex
        pdf_b64 = _b64.b64encode(upload.read()).decode('ascii')

        # Pre-seed processing state so the first poll never 404s.
        _cache.set(
            EXTRACT_INSTRUMENT_CACHE_KEY_FMT.format(job_id=job_id),
            {'status': 'processing', 'progress': 1, 'message': 'Queued for extraction…'},
            EXTRACT_TAG_RESULT_TTL_S,
        )

        task_args = (job_id, pdf_b64, upload.name, provider, api_key, request.user.id)
        try:
            run_extract_instrument_tags_task.delay(*task_args)
        except Exception as exc:
            # Broker unavailable (local dev without Celery) — run in a daemon thread.
            logger.warning('[PIDV2InstrExtract] Celery dispatch failed, using thread: %s', exc)
            _threading.Thread(
                target=run_extract_instrument_tags_task,
                args=task_args,
                daemon=True,
            ).start()

        return Response({
            'async':    True,
            'job_id':   job_id,
            'status':   'processing',
            'poll_url': f'/api/v1/pid_checker_v2/extract-instrument-tags/status/{job_id}/',
        }, status=status.HTTP_202_ACCEPTED)


class ExtractInstrumentTagsStatusView(APIView):
    """Poll endpoint for ``run_extract_instrument_tags_task``."""

    permission_classes = [IsAuthenticated]

    def get(self, request, job_id, *args, **kwargs):
        from django.core.cache import cache as _cache
        from .tasks import EXTRACT_INSTRUMENT_CACHE_KEY_FMT

        entry = _cache.get(EXTRACT_INSTRUMENT_CACHE_KEY_FMT.format(job_id=job_id))
        if entry is None:
            return Response(
                {'status': 'unknown', 'error': 'job not found or expired'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(entry, status=status.HTTP_200_OK)


# ─── Token usage endpoints ───────────────────────────────────────────
USAGE_LIST_PAGE_SIZE = 200
USAGE_LIST_MAX_PAGE_SIZE = 1000


class UsageLogListView(ListAPIView):
    """List the current user's token usage rows (most recent first)."""
    permission_classes = [IsAuthenticated]
    serializer_class = PidCheckerV2UsageLogSerializer

    def get_queryset(self):
        qs = PidCheckerV2UsageLog.objects.filter(created_by=self.request.user)
        feature = self.request.query_params.get('feature')
        if feature:
            qs = qs.filter(feature=feature)
        since = self.request.query_params.get('since')
        until = self.request.query_params.get('until')
        from datetime import datetime as _dt
        if since:
            try:
                qs = qs.filter(created_at__date__gte=_dt.strptime(since, '%Y-%m-%d').date())
            except ValueError:
                pass
        if until:
            try:
                qs = qs.filter(created_at__date__lte=_dt.strptime(until, '%Y-%m-%d').date())
            except ValueError:
                pass
        try:
            page_size = min(int(self.request.query_params.get('page_size') or USAGE_LIST_PAGE_SIZE),
                            USAGE_LIST_MAX_PAGE_SIZE)
        except (TypeError, ValueError):
            page_size = USAGE_LIST_PAGE_SIZE
        return qs.order_by('-created_at')[:page_size]


class UsageSummaryView(APIView):
    """Aggregate usage for the current user (optionally within a date range)."""
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = PidCheckerV2UsageLog.objects.filter(created_by=request.user)
        since = request.query_params.get('since')
        until = request.query_params.get('until')
        from datetime import datetime as _dt
        if since:
            try:
                qs = qs.filter(created_at__date__gte=_dt.strptime(since, '%Y-%m-%d').date())
            except ValueError:
                pass
        if until:
            try:
                qs = qs.filter(created_at__date__lte=_dt.strptime(until, '%Y-%m-%d').date())
            except ValueError:
                pass
        from decimal import Decimal
        from collections import defaultdict
        total = {'calls': 0, 'input_tokens': 0, 'output_tokens': 0,
                 'total_tokens': 0, 'cost_usd': Decimal('0')}
        by_feature = defaultdict(lambda: {'calls': 0, 'input_tokens': 0,
                                          'output_tokens': 0, 'total_tokens': 0,
                                          'cost_usd': Decimal('0')})
        by_model = defaultdict(lambda: {'calls': 0, 'input_tokens': 0,
                                        'output_tokens': 0, 'total_tokens': 0,
                                        'cost_usd': Decimal('0')})
        for r in qs.only('feature', 'provider', 'model_name', 'call_count',
                         'input_tokens', 'output_tokens', 'total_tokens',
                         'cost_usd'):
            for bucket in (total, by_feature[r.feature or ''],
                           by_model[f'{r.provider}|{r.model_name}']):
                bucket['calls'] += r.call_count
                bucket['input_tokens'] += r.input_tokens
                bucket['output_tokens'] += r.output_tokens
                bucket['total_tokens'] += r.total_tokens
                bucket['cost_usd'] += r.cost_usd or Decimal('0')

        def _fmt(b):
            return {**b, 'cost_usd': str(b['cost_usd'])}

        return Response({
            'total': _fmt(total),
            'by_feature': {k: _fmt(v) for k, v in by_feature.items()},
            'by_model': {k: _fmt(v) for k, v in by_model.items()},
            'row_count': qs.count(),
        })


class TokenReportView(APIView):
    """Generate a consolidated token report (Excel or PDF)."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        fmt = (request.data.get('format') or 'xlsx').lower()
        if fmt not in ('xlsx', 'pdf'):
            return Response({'error': "format must be 'xlsx' or 'pdf'"},
                            status=status.HTTP_400_BAD_REQUEST)
        qs = PidCheckerV2UsageLog.objects.filter(created_by=request.user)
        since = request.data.get('since')
        until = request.data.get('until')
        from datetime import datetime as _dt
        if since:
            try:
                qs = qs.filter(created_at__date__gte=_dt.strptime(since, '%Y-%m-%d').date())
            except ValueError:
                pass
        if until:
            try:
                qs = qs.filter(created_at__date__lte=_dt.strptime(until, '%Y-%m-%d').date())
            except ValueError:
                pass
        qs = qs.order_by('-created_at')

        from .services.token_report import build_xlsx, build_pdf
        try:
            if fmt == 'xlsx':
                data = build_xlsx(qs)
                content_type = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                filename = f'pid_checker_v2_token_report_{_dt.utcnow().strftime("%Y%m%d_%H%M%S")}.xlsx'
            else:
                data = build_pdf(qs)
                content_type = 'application/pdf'
                filename = f'pid_checker_v2_token_report_{_dt.utcnow().strftime("%Y%m%d_%H%M%S")}.pdf'
        except Exception as exc:
            logger.exception('Token report generation failed')
            return Response({'error': f'report generation failed: {exc}'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        from django.http import HttpResponse
        resp = HttpResponse(data, content_type=content_type)
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp

