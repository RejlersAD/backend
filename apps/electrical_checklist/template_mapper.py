"""
Template mapper: converts raw ExtractedField list -> 6-column checklist structure.

The output shape is IDENTICAL to the frontend's `checklistData` state so the
detailed template view can `setChecklistData(result.checklist_data)` directly:

    {
      "<field_id>": {
        "field_name":    "<label>",
        "site_value":    "<value from source>",
        "remarks":       "<remark from source>",
        "need_list":     "<need-list from source>",
        "query":         "<query from source>",
        "company_reply": "",                 # always empty \u2014 filled by company later
        "_meta": {
          "confidence":  85,
          "page":        2,
          "source_file": "Part_01.pdf",
          "engineer":    "Habil, Shayad Abdul Razaq",
          "method":      "vision",           # ocr | vision | vision_escalated
        }
      },
      ...
    }

Also produces summary statistics used by the extraction job model.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Optional

from .handwriting_extractor import ExtractedField, ExtractionResult
from .template_v2_config import (
    EXTRACTABLE_COLUMNS,
    HANDWRITING_CONFIG,
    TEMPLATE_V2_SECTIONS,
    get_empty_template_v2_data,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Merge strategies (soft-coded via HANDWRITING_CONFIG["merge_strategy"])
# ─────────────────────────────────────────────────────────────────────────────

def _should_replace(existing: Optional[ExtractedField],
                    incoming: ExtractedField,
                    strategy: str) -> bool:
    """Return True if incoming field should overwrite existing one."""
    if existing is None:
        return True
    if strategy == "first_wins":
        return False
    if strategy == "last_wins":
        return True
    # default: highest_confidence
    return incoming.confidence > existing.confidence


def _decode_value_json(raw_value: str) -> Dict[str, str]:
    """Extractor stores value as JSON string with 4 extractable columns."""
    try:
        parsed = json.loads(raw_value) if raw_value else {}
    except (json.JSONDecodeError, TypeError):
        parsed = {"site_value": str(raw_value or "")}

    return {
        col: str(parsed.get(col) or "").strip()
        for col in EXTRACTABLE_COLUMNS
    }


# ─────────────────────────────────────────────────────────────────────────────
# Main mapper
# ─────────────────────────────────────────────────────────────────────────────

def map_extractions_to_template(
    extraction_results: Iterable[ExtractionResult],
    engineer_name: str = "",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Merge extraction results from N source files into the 6-column template.

    Args:
        extraction_results: Iterable of ExtractionResult (one per PDF).
        engineer_name: Attribution string for `_meta.engineer`.
        config: Optional config overrides.

    Returns:
        {
            "checklist_data": <6-column dict, ready for frontend>,
            "summary": {...statistics...},
            "sources": [{"file": ..., "method": ..., "pages": N, "fields": N}, ...],
        }
    """
    cfg = {**HANDWRITING_CONFIG, **(config or {})}
    strategy = cfg.get("merge_strategy", "highest_confidence")

    # Best ExtractedField per field_id after applying merge strategy across all files.
    best_by_field: Dict[str, ExtractedField] = {}
    sources_summary: List[Dict[str, Any]] = []

    for result in extraction_results:
        source_file = ""
        file_field_count = 0
        for f in result.fields:
            source_file = source_file or f.source_file
            if _should_replace(best_by_field.get(f.field_id), f, strategy):
                best_by_field[f.field_id] = f
                file_field_count += 1

        sources_summary.append({
            "file":            source_file or "unknown",
            "method":          result.method_used,
            "pages_processed": result.pages_processed,
            "fields_found":    len(result.fields),
            "fields_accepted": file_field_count,
            "errors":          result.errors,
        })

    # Build 6-column output starting from the empty template so unfilled fields
    # still show up in the frontend view.
    checklist_data = get_empty_template_v2_data()

    for field_id, extracted in best_by_field.items():
        if field_id not in checklist_data:
            # Should never happen (extractor filters by valid IDs) but be defensive.
            continue

        cols = _decode_value_json(extracted.value)
        row = checklist_data[field_id]
        for col_key, col_val in cols.items():
            if col_val:  # never overwrite label with empty string
                row[col_key] = col_val

        row["_meta"] = {
            "confidence":  extracted.confidence,
            "page":        extracted.page,
            "source_file": extracted.source_file,
            "engineer":    engineer_name,
            "method":      extracted.method,
        }

    # Summary stats
    filled_field_ids = [
        fid for fid, row in checklist_data.items()
        if any(row.get(col) for col in EXTRACTABLE_COLUMNS)
    ]
    sections_with_data = _count_sections_with_data(filled_field_ids)
    avg_confidence = _average_confidence(best_by_field.values())

    summary = {
        "fields_extracted":   len(filled_field_ids),
        "total_fields":       sum(len(s["fields"]) for s in TEMPLATE_V2_SECTIONS),
        "sections_completed": sections_with_data,
        "total_sections":     len(TEMPLATE_V2_SECTIONS),
        "signatures_found":   0,   # signatures handled by existing pipeline
        "confidence_score":   avg_confidence,
        "merge_strategy":     strategy,
    }

    logger.info(
        "[TemplateMapper] Mapped %d/%d fields across %d sections (avg conf=%d) from %d file(s)",
        summary["fields_extracted"], summary["total_fields"],
        summary["sections_completed"], summary["confidence_score"],
        len(sources_summary),
    )

    return {
        "checklist_data": checklist_data,
        "summary":        summary,
        "sources":        sources_summary,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _count_sections_with_data(filled_field_ids: List[str]) -> int:
    filled = set(filled_field_ids)
    count = 0
    for section in TEMPLATE_V2_SECTIONS:
        if any(f["id"] in filled for f in section["fields"]):
            count += 1
    return count


def _average_confidence(fields: Iterable[ExtractedField]) -> int:
    fields = list(fields)
    if not fields:
        return 0
    return round(sum(f.confidence for f in fields) / len(fields))
