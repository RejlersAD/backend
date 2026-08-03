"""
Non-TEFF Document Search & Locator service.

Powers the "Document Search Canvas" feature: an intelligent free-text search
across all bulk-batch records (and optionally legacy single-file jobs), and a
PyMuPDF-driven locator that returns the bounding boxes (as % of page size) for
every match, so the frontend canvas can overlay highlight boxes — exactly like
the P&ID QC overlay markers, but driven on-demand from the stored PDF rather
than from pre-computed coordinates.

This module is fully additive — it never imports the extractor / batch_views
internals beyond the model layer, and never mutates a batch or item.

All knobs live in ``SEARCH_CONFIG`` and ``LOCATE_CONFIG`` so behaviour can be
re-tuned by editing constants only — no code changes required.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from django.conf import settings
from django.db.models import Q

from ..models import NonTeffBatch, NonTeffBatchItem, NonTeffExtractionJob

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Soft-coded configuration
# ---------------------------------------------------------------------------

SEARCH_CONFIG: Dict[str, Any] = {
    # Maximum results returned per search call.
    "max_results": 200,
    # Minimum query length (after strip) — shorter queries are rejected.
    "min_query_length": 2,
    # Fields searched on NonTeffBatchItem.fields (master_index_template keys).
    # Order = display priority for the "matched_field" column.
    "batch_searchable_fields": [
        "tag", "document_number", "document_title", "equipment_no",
        "instrument_tag_no", "line_number", "po_no", "vendor_ref",
        "contractor_ref", "vendor_name", "originator", "revision",
        "document_type", "document_subtype", "discipline", "area", "unit",
        "project_title", "transmittal_no", "agreement_no",
    ],
    # Fields searched on NonTeffExtractionJob.result_json.items[*].
    "job_searchable_fields": [
        "document_no", "document_title", "instrument_tag_no", "line_number",
        "equipment_no", "mechanical_component", "originator", "revision",
        "discipline", "status", "remarks",
    ],
    # When a hit comes from file_name fall-back, label it as such.
    "filename_field_label": "file_name",
}

LOCATE_CONFIG: Dict[str, Any] = {
    # Render DPI for cached page images — 144 is a sweet-spot between
    # readability and cache size for A3/A1 engineering drawings.
    "render_dpi": 144,
    # Cache directory under MEDIA_ROOT for rendered page PNGs.
    "image_cache_subdir": "non_teff_search_cache",
    # Maximum pages to scan in a single locate call (defensive).
    "max_pages_per_locate": 50,
    # If True, also locate substrings of the query (case-insensitive) when
    # PyMuPDF's exact search returns no hits.
    "fallback_substring_search": True,
    # Minimum substring length to attempt fallback OCR-text search.
    "fallback_min_length": 3,
}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def _normalize_query(q: str) -> str:
    return (q or "").strip()


def _matches_value(value: Any, q_lower: str) -> Optional[str]:
    """Return the stringified value if it contains q_lower, else None."""
    if value in (None, ""):
        return None
    s = str(value)
    return s if q_lower in s.lower() else None


def _snippet(text: str, q_lower: str, radius: int = 40) -> str:
    if not text:
        return ""
    idx = text.lower().find(q_lower)
    if idx < 0:
        return text[: 2 * radius]
    start = max(0, idx - radius)
    end = min(len(text), idx + len(q_lower) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def _scan_batch_items(
    query: str,
    batch_id: Optional[str],
    limit: int,
) -> List[Dict[str, Any]]:
    """Search NonTeffBatchItem rows. Uses DB icontains for the cheap pass."""
    q_lower = query.lower()
    qs = NonTeffBatchItem.objects.select_related("batch").all()
    if batch_id:
        qs = qs.filter(batch_id=batch_id)
    # Coarse DB filter — file_name OR any character in fields JSON.
    # Postgres JSONB icontains works on the serialized text representation.
    qs = qs.filter(
        Q(file_name__icontains=query) | Q(fields__icontains=query)
    ).order_by("file_name")[: limit * 2]  # over-fetch then refine in Python.

    results: List[Dict[str, Any]] = []
    fields_priority = SEARCH_CONFIG["batch_searchable_fields"]
    for item in qs:
        fields = item.fields or {}
        matched_field: Optional[str] = None
        matched_value: Optional[str] = None

        for key in fields_priority:
            hit = _matches_value(fields.get(key), q_lower)
            if hit is not None:
                matched_field, matched_value = key, hit
                break
        # Fall back: any other field, then file_name.
        if matched_field is None:
            for key, val in fields.items():
                hit = _matches_value(val, q_lower)
                if hit is not None:
                    matched_field, matched_value = key, hit
                    break
        if matched_field is None and q_lower in (item.file_name or "").lower():
            matched_field = SEARCH_CONFIG["filename_field_label"]
            matched_value = item.file_name
        if matched_field is None:
            continue

        results.append({
            "kind": "batch",
            "batch_id": str(item.batch_id),
            "batch_name": item.batch.name if item.batch_id else "",
            "item_id": str(item.item_id),
            "file_name": item.file_name,
            "status": item.status,
            "matched_field": matched_field,
            "matched_value": matched_value,
            "snippet": _snippet(matched_value or "", q_lower),
            "fields": fields,
        })
        if len(results) >= limit:
            break
    return results


def _scan_jobs(query: str, limit: int) -> List[Dict[str, Any]]:
    """Search legacy NonTeffExtractionJob rows (single-file workflow)."""
    q_lower = query.lower()
    qs = NonTeffExtractionJob.objects.filter(
        status=NonTeffExtractionJob.STATUS_COMPLETED,
    ).order_by("-created_at")[: limit * 4]
    results: List[Dict[str, Any]] = []
    fields_priority = SEARCH_CONFIG["job_searchable_fields"]
    for job in qs:
        result = job.result_json or {}
        items = result.get("items") or []
        for row_idx, row in enumerate(items):
            if not isinstance(row, dict):
                continue
            matched_field = matched_value = None
            for key in fields_priority:
                hit = _matches_value(row.get(key), q_lower)
                if hit is not None:
                    matched_field, matched_value = key, hit
                    break
            if matched_field is None:
                continue
            results.append({
                "kind": "job",
                "batch_id": None,
                "batch_name": "",
                "job_id": str(job.job_id),
                "row_index": row_idx,
                "file_name": job.file_name,
                "status": job.status,
                "matched_field": matched_field,
                "matched_value": matched_value,
                "snippet": _snippet(matched_value or "", q_lower),
                "fields": row,
            })
            if len(results) >= limit:
                return results
    return results


def search_records(
    query: str,
    batch_id: Optional[str] = None,
    kind: Optional[str] = None,
) -> Dict[str, Any]:
    """Public entry-point. Returns ``{query, total, results}``."""
    query = _normalize_query(query)
    if len(query) < SEARCH_CONFIG["min_query_length"]:
        return {"query": query, "total": 0, "results": [], "error": "query too short"}
    limit = SEARCH_CONFIG["max_results"]
    out: List[Dict[str, Any]] = []
    if kind in (None, "batch"):
        out.extend(_scan_batch_items(query, batch_id, limit))
    if kind in (None, "job") and not batch_id and len(out) < limit:
        out.extend(_scan_jobs(query, limit - len(out)))
    return {"query": query, "total": len(out), "results": out[:limit]}


# ---------------------------------------------------------------------------
# PDF locator + page rendering
# ---------------------------------------------------------------------------

def _open_pdf(path: str):
    """Lazy import PyMuPDF and return an open document, or None if unavailable."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        logger.warning("PyMuPDF not installed — locator disabled")
        return None
    if not path or not os.path.exists(path):
        return None
    try:
        return fitz.open(path)
    except Exception:
        logger.exception("Failed to open PDF for locate: %s", path)
        return None


def _rect_to_pct(rect, page_width: float, page_height: float) -> Dict[str, float]:
    if not page_width or not page_height:
        return {"x": 0, "y": 0, "w": 0, "h": 0}
    return {
        "x": float(rect.x0) / page_width,
        "y": float(rect.y0) / page_height,
        "w": float(rect.x1 - rect.x0) / page_width,
        "h": float(rect.y1 - rect.y0) / page_height,
    }


def locate_in_pdf(file_path: str, query: str) -> Dict[str, Any]:
    """Return per-page bounding boxes (as page-size percentages) for *query*."""
    query = _normalize_query(query)
    if not query:
        return {"page_count": 0, "matches": []}
    doc = _open_pdf(file_path)
    if doc is None:
        return {"page_count": 0, "matches": [], "error": "pdf unavailable"}
    matches: List[Dict[str, Any]] = []
    max_pages = min(doc.page_count, LOCATE_CONFIG["max_pages_per_locate"])
    try:
        for page_no in range(max_pages):
            page = doc.load_page(page_no)
            rects = page.search_for(query) or []
            if not rects and LOCATE_CONFIG["fallback_substring_search"] \
                    and len(query) >= LOCATE_CONFIG["fallback_min_length"]:
                # Word-by-word fallback: split query and search each token,
                # then keep rectangles whose union contains all tokens.
                tokens = [t for t in re.split(r"\s+", query) if t]
                if len(tokens) > 1:
                    token_rects = []
                    for tok in tokens:
                        token_rects.extend(page.search_for(tok) or [])
                    rects = token_rects
            for rect in rects:
                matches.append({
                    "page": page_no + 1,  # 1-based for display
                    "rect_pct": _rect_to_pct(rect, page.rect.width, page.rect.height),
                    "page_width": page.rect.width,
                    "page_height": page.rect.height,
                })
        return {"page_count": doc.page_count, "matches": matches}
    finally:
        doc.close()


def _cache_root() -> str:
    return os.path.join(
        settings.MEDIA_ROOT, LOCATE_CONFIG["image_cache_subdir"]
    )


def render_pdf_page_png(file_path: str, page_no: int, item_key: str) -> Optional[str]:
    """Render *page_no* (1-based) of *file_path* to a cached PNG; return path."""
    if page_no < 1:
        return None
    cache_dir = os.path.join(_cache_root(), str(item_key))
    os.makedirs(cache_dir, exist_ok=True)
    dpi = int(LOCATE_CONFIG["render_dpi"])
    out_path = os.path.join(cache_dir, f"p{page_no}_dpi{dpi}.png")
    if os.path.exists(out_path):
        return out_path
    doc = _open_pdf(file_path)
    if doc is None:
        return None
    try:
        if page_no > doc.page_count:
            return None
        page = doc.load_page(page_no - 1)
        zoom = dpi / 72.0
        import fitz  # local import (already verified by _open_pdf)
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        pix.save(out_path)
        return out_path
    except Exception:
        logger.exception("Failed to render page %s of %s", page_no, file_path)
        return None
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# Resolver helpers
# ---------------------------------------------------------------------------

def resolve_item_pdf(item: NonTeffBatchItem) -> Tuple[Optional[str], Optional[str]]:
    """Return (absolute_path, error). Only PDFs are locatable."""
    path = item.storage_key or ""
    if not path or not os.path.exists(path):
        return None, "file missing on disk"
    if not path.lower().endswith(".pdf"):
        return None, "locator supports PDF only"
    return path, None
