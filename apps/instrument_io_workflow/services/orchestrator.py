"""
Top-level extraction orchestrator.

Two source pipelines feed one shared finish line:

  I/O List table PDF  -> extract_io_rows_from_pages (PyMuPDF/local OCR, $0)
  P&ID drawing PDF     -> pid_vision_extractor (BYOK Vision, or local OCR
                           fallback with no key)

Both land in the same canonical IO_LIST_CANONICAL_COLUMNS row shape, so
everything downstream (legend check, xlsx export, the table UI) works
unmodified for either document type. combine_and_finalize()/
persist_extraction() are the shared tail of both the synchronous path
(extract_document, called inline from views.py for a small document) and
the asynchronous Celery chord path (tasks.py's finalize_io_document, for a
document over config.PAGE_FANOUT_THRESHOLD pages).

extract_document(pdf_bytes, user=None, force_refresh=True,
                  vision_provider=None, vision_api_key=None, thorough=False) -> {
    'sha256':        str,
    'pages':         [...],           # classified
    'comments':      [...],           # 5-column comments resolution sheet
    'io_rows':       [...],           # canonical-shaped rows
    'document_type': 'io_list' | 'pid_drawing',
    'warnings':      [...],
    'stats':         {...},
    'cost_profile':  {...},           # for transparency in the UI
}

Always runs fresh — no hash/memo cache of any kind. "Upload & Extract"
(views.py's create()) and "Re-extract" (re_extract()) both must run a
genuinely new extraction on every call; a cache here previously caused
confirmed bugs (a fresh call silently returning an earlier, unrelated
run's result — see this function's own docstring). Viewing an
already-completed document is a separate, plain DB read
(IOListDocumentViewSet.retrieve/list) that never reaches this function.

All work is cost-optimised:
  - PyMuPDF for everything by default ($0)
  - P&ID Vision is opt-in (BYOK) — local OCR fallback otherwise
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import Counter
from typing import Dict, Any

from django.db import transaction

from .page_classifier import classify_pages
from .comment_table_extractor import extract_comments_from_pages
from .io_table_extractor import (
    extract_io_rows_from_pages,
    extract_pid_drawing_row_for_page_via_local_ocr,
)
from .comment_row_linker import link_comments_to_rows

logger = logging.getLogger(__name__)


# No in-process/hash-based extraction cache — deliberately. Both
# views.py callers (create()'s "Upload & Extract" and re_extract()'s
# "Re-extract") must always run a genuinely fresh extraction; a hash
# keyed cache here previously caused two confirmed bugs: a fresh
# upload/re-extract silently returning an EARLIER run's result whenever
# the same file bytes had been seen before, even with a different
# project, a newly-supplied Vision key, or a different Quick/Thorough
# Scan mode than that earlier run used — none of those were ever part of
# the cache key. Viewing an already-completed document doesn't need a
# cache either: that's a plain DB read (IOListDocumentViewSet.retrieve/
# list, via the serializers) that never calls this function at all.

# Title-block phrases that mark a page as a P&ID drawing when there is no
# structured io_table/io_drawing page (and therefore no io_rows) to decide
# from. Kept as one list so tasks.py's dispatch-time approximation
# (_looks_like_pid_drawing) can import it rather than duplicating it.
_PID_DRAWING_TITLE_PHRASES = [
    'piping & instrument diagram',
    'piping and instrument diagram',
    'piping & instrumentation diagram',
    'piping and instrumentation diagram',
    'p&id',
    'p & i d',
]


def sha256_of(pdf_bytes: bytes) -> str:
    return hashlib.sha256(pdf_bytes).hexdigest()


def _detect_document_type(pages: list, io_rows: list) -> str:
    """'io_list' vs 'pid_drawing'. Any page classified as a structured
    table page (io_table/io_drawing) that actually yielded rows is treated
    as decisive — this is the common case and stays exactly as accurate as
    the table-only pipeline always was. Otherwise, fall back to a P&ID
    title-block phrase check on the combined page text. Defaults to
    'io_list' if neither signal is clear, so nothing that worked before
    this document-type split regresses.
    """
    has_table_page = any(
        p.get('page_type') in ('io_table', 'io_drawing') for p in pages
    )
    if has_table_page and io_rows:
        return 'io_list'
    combined_text = ' '.join((p.get('text') or '') for p in pages).lower()
    if any(phrase in combined_text for phrase in _PID_DRAWING_TITLE_PHRASES):
        return 'pid_drawing'
    return 'io_list'


def _active_legends_for_user(user):
    """{section: IOListLegendSheet} for every section this user currently
    has an active legend in. Returns {} for an anonymous/None user — legend
    checking is purely additive, never required.

    Falls back to the shared default (is_default=True, is_active=True —
    seeded by seed_io_default_legends) for any section the user has no
    active legend of their own in, so legend checking works out of the box
    for every user, not just whoever's account the seed data happened to
    land under historically. The user's own legend for a section always
    wins over the default when both exist — same priority order as
    views.py's IOListLegendSheetListCreateView.get_queryset.
    """
    from ..models import IOListLegendSheet

    if not user or not getattr(user, 'is_authenticated', False):
        return {}
    own = {
        legend.section: legend
        for legend in IOListLegendSheet.objects.filter(
            created_by=user, is_active=True,
        )
    }
    defaults = {
        legend.section: legend
        for legend in IOListLegendSheet.objects.filter(is_default=True, is_active=True)
        if legend.section not in own
    }
    return {**defaults, **own}


def _run_legend_check(io_rows: list, comments: list, user) -> list:
    """Runs every active-legend check (tag-format, code-lookup, symbol-
    lookup) this user has configured, against the extracted rows/comments.
    Never raises — a structurally invalid legend definition is skipped,
    not fatal, since this is an additive step inside document extraction.
    """
    from ..models import (
        IO_LEGEND_FORMAT_SECTIONS, IO_LEGEND_LOOKUP_SECTIONS,
        IO_LEGEND_SYMBOL_LOOKUP_SECTIONS,
    )
    from .legend_comparison import compare_io_with_legends, check_symbol_types_against_legends

    try:
        active = _active_legends_for_user(user)
        if not active:
            return []

        format_legends = {
            section: legend.definition
            for section, legend in active.items()
            if section in IO_LEGEND_FORMAT_SECTIONS
        }
        lookup_legends = {
            section: (legend.definition, field_name)
            for section, legend in active.items()
            if section in IO_LEGEND_LOOKUP_SECTIONS
            for field_name in [IO_LEGEND_LOOKUP_SECTIONS[section]]
        }
        symbol_legends = {
            section: (legend.definition, 'symbol_type')
            for section, legend in active.items()
            if section in IO_LEGEND_SYMBOL_LOOKUP_SECTIONS
        }

        findings = compare_io_with_legends(io_rows, comments, format_legends, lookup_legends)
        if symbol_legends:
            findings += check_symbol_types_against_legends(io_rows, symbol_legends)
        return findings
    except Exception:
        logger.exception('[IOWF] Legend check failed — continuing without it')
        return []


def combine_and_finalize(
    digest: str, pages: list, comments: list, io_rows: list,
    uploaded_by, started: float,
    document_type: str = 'io_list', warnings: list | None = None,
    document_id=None,
) -> Dict[str, Any]:
    """Shared tail of both the synchronous and Celery-chord extraction
    paths — link comments<->rows, run the legend check, build stats. Never
    persists the RESULT itself (see persist_extraction).

    document_id (optional): when given, writes real, live
    IOListDocument.current_phase updates as this function actually enters
    each sub-step — GET .../status/ (views.py) surfaces these for the
    frontend's progress display. None (the default) skips these writes
    entirely, e.g. for a throwaway/test call with no real document row —
    never raises either way if the update itself fails, since a
    phase-tracking write must never fail the extraction it's describing.
    """
    def _set_phase(phase: str) -> None:
        if not document_id:
            return
        try:
            from ..models import IOListDocument
            IOListDocument.objects.filter(id=document_id).update(current_phase=phase)
        except Exception:
            logger.exception('[IOWF] Failed to update current_phase=%r for document %s', phase, document_id)

    if document_type == 'io_list':
        _set_phase('linking_comments')
        link_comments_to_rows(comments, io_rows)

    _set_phase('validating_legend')
    legend_findings = _run_legend_check(io_rows, comments, uploaded_by)

    comment_page_idx = [p['page_index'] for p in pages if p['page_type'] == 'comments_sheet']
    io_page_idx = [p['page_index'] for p in pages if p['page_type'] in ('io_table', 'io_drawing')]

    result: Dict[str, Any] = {
        'sha256':        digest,
        'pages': [
            {'page_index': p['page_index'], 'page_type': p['page_type'], 'hits': p.get('hits', [])}
            for p in pages
        ],
        'comments':      comments,
        'io_rows':       io_rows,
        'document_type': document_type,
        'legend_findings': legend_findings,
        'warnings':      warnings or [],
        'stats': {
            'total_pages':          len(pages),
            'comment_pages':        len(comment_page_idx),
            'io_table_pages':       len(io_page_idx),
            'comments_found':       len(comments),
            'io_rows_found':        len(io_rows),
            'legend_findings_found': len(legend_findings),
            'local_ocr_rows_found': sum(
                1 for row in io_rows
                if 'local OCR' in (row.get('remarks') or '')
            ),
            'linked_comments':      sum(1 for c in comments if c.get('linked_tags')),
            'elapsed_seconds':      round(time.time() - started, 2),
            'status_code_breakdown': dict(
                Counter(
                    (c.get('status_code') or '').strip() or 'unknown'
                    for c in comments
                )
            ),
        },
        'cost_profile': {
            'cache_hit':            False,
            # BUG FIX: the frontend's CostBadge (IOListWorkflowPage.jsx)
            # reads `vision_fallback_used` specifically — this key was
            # named `vision_used` here, so the badge's check
            # (`profile.vision_fallback_used ? 'vision' : ...`) was always
            # undefined/falsy and silently fell through to "Free ·
            # PyMuPDF" even when Vision genuinely ran and produced real
            # rows. The detection itself was always correct (every
            # successful Vision row's remarks contains "AI Vision" — see
            # pid_vision_extractor._row_from_tag_info) — only the key name
            # was wrong.
            'vision_fallback_used': any(
                'Vision' in (row.get('remarks') or '') for row in io_rows
            ),
            'local_ocr_used':       any(
                'local OCR' in (row.get('remarks') or '')
                for row in io_rows
            ),
        },
    }
    logger.info(
        "[IOWF] Extraction complete (%s): pages=%d comments=%d io_rows=%d "
        "legend_findings=%d elapsed=%.2fs",
        document_type, len(pages), len(comments), len(io_rows),
        len(legend_findings), result['stats']['elapsed_seconds'],
    )
    return result


def persist_extraction(document, result: Dict[str, Any]) -> None:
    """Save an extraction result onto a document row — shared by views.py
    (synchronous path) and tasks.py's finalize_io_document (Celery chord
    callback)."""
    from ..models import IOListExtractedComment, IOListExtractedRow

    with transaction.atomic():
        document.extracted_comments.all().delete()
        document.extracted_rows.all().delete()

        IOListExtractedComment.objects.bulk_create([
            IOListExtractedComment(
                document=document,
                s_no=c.get('s_no', ''),
                company_comment=c.get('company_comment', ''),
                contractor_reply=c.get('contractor_reply', ''),
                company_decision=c.get('company_decision', ''),
                status_code=c.get('status_code', ''),
                status_meaning=c.get('status_meaning', ''),
                page_number=c.get('page_number'),
                linked_tags=c.get('linked_tags', []),
            )
            for c in result.get('comments', [])
        ])

        IOListExtractedRow.objects.bulk_create([
            IOListExtractedRow(
                document=document,
                tag_number=r.get('tag_number', ''),
                page_number=r.get('page_number'),
                data={k: v for k, v in r.items()
                      if k not in ('tag_number', 'page_number')},
            )
            for r in result.get('io_rows', [])
        ])

        document.extraction_stats = {
            **result.get('stats', {}),
            'cost_profile': result.get('cost_profile', {}),
            'warnings': result.get('warnings', []),
        }
        document.legend_findings = result.get('legend_findings', [])
        document.document_type = result.get('document_type', 'io_list')
        document.status = 'completed'
        document.extraction_error = ''
        document.pdf_sha256 = result.get('sha256', document.pdf_sha256)
        document.save(update_fields=[
            'extraction_stats', 'legend_findings', 'document_type',
            'status', 'extraction_error', 'pdf_sha256', 'updated_at',
        ])


def extract_document(
    pdf_bytes: bytes, user=None, force_refresh: bool = True,
    vision_provider: str | None = None, vision_api_key: str | None = None,
    thorough: bool = False,
) -> Dict[str, Any]:
    """Synchronous extraction — used inline by views.py for any document
    at or under config.PAGE_FANOUT_THRESHOLD pages (the vast majority).
    Larger documents use tasks.process_io_document's Celery chord instead
    (see that module's docstring), which reaches the same
    combine_and_finalize/persist_extraction tail via finalize_io_document.

    Always runs a genuinely fresh extraction — there is no cache to skip
    here (see the module-level note above `extract_document`... actually
    above the imports). `force_refresh` is kept as an accepted parameter
    (defaulting to True) purely so both real callers (views.py's create()
    and re_extract()) can keep passing it explicitly/self-documentingly
    without it changing behaviour; it is not read anywhere in this
    function's body.

    vision_provider/vision_api_key/thorough are only consulted if the
    document turns out to be a P&ID drawing — ignored entirely (as before)
    for a regular I/O List table document.
    """
    started = time.time()
    digest = sha256_of(pdf_bytes)
    logger.info(
        "[IOWF] extract_document: running a fresh extraction for %s "
        "(thorough=%r, vision_provider=%r, key supplied=%s)",
        digest[:12], thorough, vision_provider, bool(vision_api_key),
    )

    pages = classify_pages(pdf_bytes)
    comment_page_idx = [p['page_index'] for p in pages if p['page_type'] == 'comments_sheet']
    io_page_idx = [p['page_index'] for p in pages if p['page_type'] in ('io_table', 'io_drawing')]

    comments = extract_comments_from_pages(pdf_bytes, comment_page_idx)
    io_rows = extract_io_rows_from_pages(pdf_bytes, io_page_idx)

    document_type = _detect_document_type(pages, io_rows)
    warnings: list = []

    if document_type == 'pid_drawing':
        # A drawing has no structured table/comments content — start from
        # a clean slate rather than whatever the table extractor guessed.
        comments = []
        if vision_provider and vision_api_key:
            try:
                from .pid_vision_extractor import extract_pid_tags_via_vision
                io_rows, vision_warnings = extract_pid_tags_via_vision(
                    pdf_bytes, vision_provider, vision_api_key, thorough=thorough,
                )
                # BUG FIX: a per-page Vision failure (bad key, rate limit,
                # network error...) used to be caught INSIDE
                # extract_pid_tags_via_vision and never re-raised, so this
                # outer except never fired and the caller silently got 0
                # (or fewer) rows with no warning at all — an invalid key
                # looked identical to "nothing on this drawing to
                # extract". That function now returns its own warnings
                # for exactly this case; surface them here.
                warnings.extend(vision_warnings)
            except Exception:
                logger.exception(
                    '[IOWF] Vision extraction failed outright — falling back to local OCR',
                )
                io_rows = []
                import fitz
                doc = fitz.open(stream=pdf_bytes, filetype='pdf')
                page_count = doc.page_count
                doc.close()
                for idx in range(page_count):
                    io_rows.extend(extract_pid_drawing_row_for_page_via_local_ocr(pdf_bytes, idx))
                warnings.append('Vision extraction failed — used local OCR instead')
        else:
            import fitz
            doc = fitz.open(stream=pdf_bytes, filetype='pdf')
            page_count = doc.page_count
            doc.close()
            io_rows = []
            for idx in range(page_count):
                io_rows.extend(extract_pid_drawing_row_for_page_via_local_ocr(pdf_bytes, idx))
            warnings.append('Add an API key for better accuracy')

    result = combine_and_finalize(
        digest, pages, comments, io_rows, user, started,
        document_type=document_type, warnings=warnings,
    )
    return result
