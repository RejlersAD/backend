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
    MODE_OCR,
    MODE_VISION,
)
from .serializers import (
    PidCheckerV2ExtractionListSerializer,
    PidCheckerV2ExtractionDetailSerializer,
    PidCheckerV2LegendSheetSerializer,
)
from .legend_defaults import (
    SECTIONS as LEGEND_SECTIONS,
    SECTION_LINE_LIST,
    get_default_template,
)
from .services.legend_engine import compile_legend, build_prompt_block
from .services.legend_validator import validate_tags as validate_tags_against_legend
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
