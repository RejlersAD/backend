"""
Workbook Chatbot Service
========================

Natural-language editing for SPEC/CAT workbook previews.

Implements a practical hybrid RAG + CAG pipeline using soft-coded configuration:
- RAG: retrieve relevant workbook rows/columns + uploaded document chunks
- CAG: cache retrieval corpus and instruction results to speed repeats

The service is deterministic and safe by default; it only executes explicit
"set/update ... where ..." style instructions.
"""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from typing import Any

from django.core.cache import cache
from django.utils import timezone

from .exporters.workbook_preview import WORKBOOK_CAT, WORKBOOK_SPEC, build_preview
from ..workbook_storage_service import batch_save_cells, get_workbook_revision


WORKBOOK_CHATBOT_CONFIG = {
    "enabled": os.getenv("SPEC_WORKBOOK_CHATBOT_ENABLED", "1").strip() not in {"0", "false", "False", "off"},
    "retrieval": {
        "max_rows_per_sheet": int(os.getenv("SPEC_CHATBOT_MAX_ROWS_PER_SHEET", "1200") or 1200),
        "top_k": int(os.getenv("SPEC_CHATBOT_TOP_K", "25") or 25),
        "min_token_len": 2,
    },
    "safety": {
        "max_cell_updates": int(os.getenv("SPEC_CHATBOT_MAX_CELL_UPDATES", "300") or 300),
        "max_instruction_chars": int(os.getenv("SPEC_CHATBOT_MAX_INSTRUCTION_CHARS", "1200") or 1200),
    },
    "cache": {
        "enabled": True,
        "ttl_seconds": int(os.getenv("SPEC_CHATBOT_CACHE_TTL_SECONDS", "900") or 900),
        "index_prefix": "spec_chatbot:index:",
        "doc_index_prefix": "spec_chatbot:doc_index:",
        "result_prefix": "spec_chatbot:result:",
    },
    "document_rag": {
        "enabled": os.getenv("SPEC_CHATBOT_DOCUMENT_RAG_ENABLED", "1").strip() not in {"0", "false", "False", "off"},
        "max_pages_scan": int(os.getenv("SPEC_CHATBOT_DOC_MAX_PAGES", "80") or 80),
        "chunk_chars": int(os.getenv("SPEC_CHATBOT_DOC_CHUNK_CHARS", "900") or 900),
        "chunk_overlap": int(os.getenv("SPEC_CHATBOT_DOC_CHUNK_OVERLAP", "120") or 120),
        "top_k_chunks": int(os.getenv("SPEC_CHATBOT_DOC_TOP_K", "8") or 8),
        "evidence_preview_chars": int(os.getenv("SPEC_CHATBOT_DOC_EVIDENCE_PREVIEW", "180") or 180),
    },
    "auto_value": {
        "enabled": True,
        "keywords": [
            "auto",
            "from document",
            "as per document",
            "as per uploaded document",
            "from uploaded document",
            "from spec",
        ],
    },
}


_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-\./]+")


@dataclass
class ParsedInstruction:
    target_column: str
    target_value: str
    where_column: str | None = None
    where_operator: str | None = None  # contains|equals
    where_value: str | None = None
    class_code: str | None = None
    sheet_name: str | None = None


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _tokens(text: str) -> set[str]:
    min_len = WORKBOOK_CHATBOT_CONFIG["retrieval"]["min_token_len"]
    return {
        t.lower()
        for t in _TOKEN_RE.findall(text or "")
        if len(t) >= min_len
    }


def _cache_get(key: str):
    if not WORKBOOK_CHATBOT_CONFIG["cache"]["enabled"]:
        return None
    return cache.get(key)


def _cache_set(key: str, value: Any) -> None:
    if not WORKBOOK_CHATBOT_CONFIG["cache"]["enabled"]:
        return
    cache.set(key, value, WORKBOOK_CHATBOT_CONFIG["cache"]["ttl_seconds"])


def _shorten(text: str, max_len: int) -> str:
    t = " ".join((text or "").split())
    if len(t) <= max_len:
        return t
    return t[: max(0, max_len - 3)] + "..."


def _extract_page_hint(instruction: str) -> int | None:
    m = re.search(r"\b(?:page|pg)\s*(\d{1,4})\b", instruction or "", flags=re.IGNORECASE)
    if not m:
        return None
    try:
        p = int(m.group(1))
        return p if p > 0 else None
    except Exception:
        return None


def _uploaded_document_context(job) -> dict[str, Any]:
    """Return provenance metadata for the source document (often S3-backed)."""
    doc = getattr(job, "document", None)
    if not doc:
        return {
            "available": False,
            "document_id": "",
            "source": "unknown",
            "filename": "",
            "file_url": "",
            "project_id": "",
            "title": "",
            "document_number": "",
            "s3_key": "",
            "size_bytes": 0,
            "page_count": 0,
            "sha256": "",
        }

    file_obj = getattr(doc, "file", None)
    source = "storage"
    file_name = ""
    if file_obj is not None:
        file_name = str(getattr(file_obj, "name", "") or "")
        source = "s3" if file_name.startswith("spec_customization/") else "storage"
    try:
        file_url = str(getattr(file_obj, "url", "") or "") if file_obj else ""
    except Exception:
        file_url = ""

    return {
        "available": True,
        "document_id": str(getattr(doc, "id", "") or ""),
        "source": source,
        "filename": str(getattr(doc, "original_filename", "") or ""),
        "file_url": file_url,
        "project_id": str(getattr(doc, "project_id", "") or ""),
        "title": str(getattr(doc, "title", "") or ""),
        "document_number": str(getattr(doc, "document_number", "") or ""),
        "s3_key": file_name,
        "size_bytes": int(getattr(doc, "file_size_bytes", 0) or 0),
        "page_count": int(getattr(doc, "total_pages", 0) or 0),
        "sha256": str(getattr(doc, "sha256_hash", "") or ""),
    }


def _extract_document_pages_text(job) -> list[dict[str, Any]]:
    """Read uploaded source document text pages (S3/local agnostic)."""
    if not WORKBOOK_CHATBOT_CONFIG["document_rag"]["enabled"]:
        return []

    doc = getattr(job, "document", None)
    file_obj = getattr(doc, "file", None) if doc else None
    if not file_obj:
        return []

    doc_hash = str(getattr(doc, "sha256_hash", "") or "")
    cache_key = f"{WORKBOOK_CHATBOT_CONFIG['cache']['doc_index_prefix']}{job.id}:{doc_hash}:pages"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    max_pages = WORKBOOK_CHATBOT_CONFIG["document_rag"]["max_pages_scan"]
    pages: list[dict[str, Any]] = []

    pdf_bytes = b""
    try:
        with file_obj.open("rb") as f:
            pdf_bytes = f.read()
    except Exception:
        pdf_bytes = b""

    if not pdf_bytes:
        return []

    try:
        import fitz

        with fitz.open(stream=pdf_bytes, filetype="pdf") as doc_pdf:
            total = min(doc_pdf.page_count, max_pages)
            for i in range(total):
                txt = doc_pdf.load_page(i).get_text("text") or ""
                txt = txt.strip()
                if txt:
                    pages.append({"page": i + 1, "text": txt})
    except Exception:
        try:
            import io
            import PyPDF2

            reader = PyPDF2.PdfReader(io.BytesIO(pdf_bytes))
            total = min(len(reader.pages), max_pages)
            for i in range(total):
                txt = (reader.pages[i].extract_text() or "").strip()
                if txt:
                    pages.append({"page": i + 1, "text": txt})
        except Exception:
            pages = []

    _cache_set(cache_key, pages)
    return pages


def _document_chunks(job) -> list[dict[str, Any]]:
    """Chunk document pages to build a lightweight retrieval corpus."""
    pages = _extract_document_pages_text(job)
    if not pages:
        return []

    doc_hash = str(getattr(getattr(job, "document", None), "sha256_hash", "") or "")
    cache_key = f"{WORKBOOK_CHATBOT_CONFIG['cache']['doc_index_prefix']}{job.id}:{doc_hash}:chunks"
    cached = _cache_get(cache_key)
    if cached:
        return cached

    chunk_chars = WORKBOOK_CHATBOT_CONFIG["document_rag"]["chunk_chars"]
    overlap = WORKBOOK_CHATBOT_CONFIG["document_rag"]["chunk_overlap"]

    chunks: list[dict[str, Any]] = []
    for p in pages:
        page = int(p["page"])
        text = str(p["text"])
        start = 0
        while start < len(text):
            end = min(len(text), start + chunk_chars)
            piece = text[start:end].strip()
            if piece:
                chunks.append({
                    "page": page,
                    "text": piece,
                    "tokens": _tokens(piece),
                })
            if end >= len(text):
                break
            start = max(0, end - overlap)

    _cache_set(cache_key, chunks)
    return chunks


def _retrieve_document_evidence(job, instruction: str, parsed: ParsedInstruction | None) -> list[dict[str, Any]]:
    """Retrieve top matching document chunks from uploaded source document."""
    if not WORKBOOK_CHATBOT_CONFIG["document_rag"]["enabled"]:
        return []

    chunks = _document_chunks(job)
    if not chunks:
        return []

    page_hint = _extract_page_hint(instruction)
    if page_hint is not None:
        filtered = [ch for ch in chunks if int(ch.get("page") or 0) == page_hint]
        # If the requested page is out of range, gracefully fall back to
        # all available pages instead of hard-failing with empty evidence.
        if filtered:
            chunks = filtered

    q_tokens = _tokens(instruction)
    if parsed and parsed.class_code:
        q_tokens = set(q_tokens)
        q_tokens.add(_norm(parsed.class_code))
    if parsed and parsed.where_value:
        q_tokens = set(q_tokens)
        q_tokens.update(_tokens(parsed.where_value))

    scored: list[tuple[int, dict[str, Any]]] = []
    for ch in chunks:
        sc = len(q_tokens.intersection(ch.get("tokens") or set()))
        if sc > 0:
            scored.append((sc, ch))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_k = WORKBOOK_CHATBOT_CONFIG["document_rag"]["top_k_chunks"]
    preview_len = WORKBOOK_CHATBOT_CONFIG["document_rag"]["evidence_preview_chars"]
    picked = scored[:top_k]
    if not picked:
        picked = [(0, ch) for ch in chunks[:top_k]]

    return [
        {
            "page": item["page"],
            "score": score,
            "snippet": _shorten(item["text"], preview_len),
            "raw_text": item["text"],
        }
        for score, item in picked
    ]


def _looks_like_auto_value(raw: str) -> bool:
    if not WORKBOOK_CHATBOT_CONFIG["auto_value"]["enabled"]:
        return False
    value = _norm(raw)
    if not value:
        return False
    return any(k in value for k in WORKBOOK_CHATBOT_CONFIG["auto_value"]["keywords"])


def _derive_value_from_evidence(column_name: str, evidence: list[dict[str, Any]]) -> str | None:
    """Infer practical values from document evidence for common workbook fields."""
    if not evidence:
        return None

    joined = "\n".join((e.get("raw_text") or e.get("snippet") or "") for e in evidence)
    col = _norm(column_name)

    material_patterns = [
        r"\bASTM\s+[A-Z0-9\-]+(?:\s+GR\.?\s*[A-Z0-9\-]+)?\b",
        r"\bASME\s+[A-Z0-9\-]+(?:\s+GR\.?\s*[A-Z0-9\-]+)?\b",
        r"\bSA[\-\s]?[0-9]{2,4}(?:\s*GR\.?\s*[A-Z0-9\-]+)?\b",
        r"\bSS\s*3(?:04|16)L?\b",
        r"\bAISI\s*3(?:04|16)L?\b",
        r"\bCS\b",
        r"\bLTCS\b",
        r"\bDUPLEX\b",
    ]
    commodity_patterns = [
        r"\b[A-Z]{2,6}-[0-9]{2,6}\b",
        r"\b[A-Z]{2,6}_[0-9]{2,6}\b",
    ]
    schedule_patterns = [
        r"\bSCH\s*[0-9]{1,3}(?:[A-Z]+)?\b",
        r"\bCLASS\s*[0-9]{2,4}\b",
        r"\bCL\s*[0-9]{2,4}\b",
    ]
    end_connection_patterns = [
        r"\bBUTT\s*WELD\b", r"\bBW\b",
        r"\bSOCKET\s*WELD\b", r"\bSW\b",
        r"\bTHREADED\b", r"\bTHREAD\b", r"\bNPT\b", r"\bTHR\b",
        r"\bRAISED\s*FACE\b", r"\bRF\b",
        r"\bFLAT\s*FACE\b", r"\bFF\b",
    ]
    flange_facing_patterns = [
        r"\bRAISED\s*FACE\b", r"\bRF\b",
        r"\bFLAT\s*FACE\b", r"\bFF\b",
        r"\bRING\s*TYPE\s*JOINT\b", r"\bRTJ\b",
    ]
    pressure_patterns = [
        r"\bCLASS\s*[0-9]{2,4}\b",
        r"\bCL\s*[0-9]{2,4}\b",
        r"\bPN\s*[0-9]{1,3}\b",
    ]
    temperature_patterns = [
        r"\b-?[0-9]{1,4}\s*(?:°\s*)?[CF]\b",
    ]
    corrosion_patterns = [
        r"\b[0-9]+(?:\.[0-9]+)?\s*mm\b",
        r"\bCA\s*[=:]?\s*[0-9]+(?:\.[0-9]+)?\s*mm\b",
    ]

    def first_match(patterns: list[str]) -> str | None:
        for pat in patterns:
            m = re.search(pat, joined, flags=re.IGNORECASE)
            if m:
                return re.sub(r"\s+", " ", m.group(0)).strip()
        return None

    def normalize_end_conn(value: str) -> str:
        t = _norm(value)
        if "butt" in t or t == "bw":
            return "BW"
        if "socket" in t or t == "sw":
            return "SW"
        if "thread" in t or "npt" in t or t == "thr":
            return "THR"
        if "raised" in t or t == "rf":
            return "RF"
        if "flat" in t or t == "ff":
            return "FF"
        return value.upper().strip()

    def normalize_facing(value: str) -> str:
        t = _norm(value)
        if "raised" in t or t == "rf":
            return "RF"
        if "flat" in t or t == "ff":
            return "FF"
        if "ring" in t or "rtj" in t:
            return "RTJ"
        return value.upper().strip()

    def normalize_pressure(value: str) -> str:
        t = re.sub(r"\s+", " ", value.upper()).strip()
        if t.startswith("CL "):
            return "CLASS " + t.split(" ", 1)[1]
        return t

    def normalize_corrosion(value: str) -> str:
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*mm", value, flags=re.IGNORECASE)
        if m:
            return f"{m.group(1)} mm"
        return re.sub(r"\s+", " ", value).strip()

    def normalize_temperature(value: str) -> str:
        return re.sub(r"\s+", "", value.upper()).replace("°", "")

    if "material" in col:
        return first_match(material_patterns)
    if "commodity" in col and "code" in col:
        return first_match(commodity_patterns)
    if "end" in col and ("connection" in col or "prep" in col):
        v = first_match(end_connection_patterns)
        return normalize_end_conn(v) if v else None
    if "flange" in col and "facing" in col:
        v = first_match(flange_facing_patterns)
        return normalize_facing(v) if v else None
    if ("pressure" in col and "rating" in col) or (col == "rating"):
        v = first_match(pressure_patterns) or first_match(schedule_patterns)
        return normalize_pressure(v) if v else None
    if "temperature" in col and "rating" in col:
        v = first_match(temperature_patterns)
        return normalize_temperature(v) if v else None
    if "corrosion" in col and "allow" in col:
        v = first_match(corrosion_patterns)
        return normalize_corrosion(v) if v else None
    if "schedule" in col or "class" in col:
        return first_match(schedule_patterns)
    if "notes" in col:
        return _shorten(joined, 160)
    return None


def _compute_confidence(*, planned_count: int, retrieved_rows: int, evidence_count: int, auto_value_used: bool) -> dict[str, Any]:
    """Heuristic confidence score for preview/apply transparency."""
    density = 0.0
    if retrieved_rows > 0:
        density = min(1.0, planned_count / max(1, retrieved_rows))

    evidence_factor = min(1.0, evidence_count / 6.0)
    score = 0.45 + (0.35 * density) + (0.15 * evidence_factor) + (0.05 if auto_value_used else 0.0)
    score = max(0.05, min(0.99, score))

    if score >= 0.8:
        label = "high"
    elif score >= 0.6:
        label = "medium"
    else:
        label = "low"

    return {
        "score": round(score, 3),
        "label": label,
        "drivers": {
            "planned_count": int(planned_count),
            "retrieved_rows": int(retrieved_rows),
            "evidence_count": int(evidence_count),
            "auto_value_used": bool(auto_value_used),
        },
    }


def _rag_cag_runtime(*, cache_hit: bool, index_cache_hits: int, workbooks: list[str], instruction: str, evidence_count: int = 0, auto_value_used: bool = False) -> dict[str, Any]:
    return {
        "rag": {
            "enabled": True,
            "strategy": "hybrid lexical retrieval (workbook + uploaded-document chunks)",
            "workbooks": workbooks,
            "instruction_tokens": sorted(_tokens(instruction)),
            "top_k": WORKBOOK_CHATBOT_CONFIG["retrieval"]["top_k"],
            "max_rows_per_sheet": WORKBOOK_CHATBOT_CONFIG["retrieval"]["max_rows_per_sheet"],
            "document_rag_enabled": bool(WORKBOOK_CHATBOT_CONFIG["document_rag"]["enabled"]),
            "document_top_k_chunks": WORKBOOK_CHATBOT_CONFIG["document_rag"]["top_k_chunks"],
            "document_evidence_count": evidence_count,
            "auto_value_used": auto_value_used,
        },
        "cag": {
            "enabled": bool(WORKBOOK_CHATBOT_CONFIG["cache"]["enabled"]),
            "result_cache_hit": bool(cache_hit),
            "retrieval_index_cache_hits": int(index_cache_hits),
            "ttl_seconds": int(WORKBOOK_CHATBOT_CONFIG["cache"]["ttl_seconds"]),
        },
    }


def _workbook_for_instruction(instruction: str, workbook_scope: str, active_workbook: str) -> list[str]:
    scope = _norm(workbook_scope)
    if scope in {WORKBOOK_SPEC, WORKBOOK_CAT}:
        return [scope]
    if scope == "both":
        return [WORKBOOK_SPEC, WORKBOOK_CAT]

    hint = _norm(instruction)
    has_spec = "spec workbook" in hint or " spec " in f" {hint} "
    has_cat = "cat workbook" in hint or " cat " in f" {hint} "
    if has_spec and has_cat:
        return [WORKBOOK_SPEC, WORKBOOK_CAT]
    if has_spec:
        return [WORKBOOK_SPEC]
    if has_cat:
        return [WORKBOOK_CAT]
    return [active_workbook if active_workbook in {WORKBOOK_SPEC, WORKBOOK_CAT} else WORKBOOK_SPEC]


def _parse_instruction(instruction: str) -> ParsedInstruction | None:
    text = (instruction or "").strip()

    p_a = re.compile(
        r"(?:set|update|change)\s+"
        r"(?P<target>[A-Za-z0-9_\- ./()]+?)\s+"
        r"(?:to|=)\s+"
        r"(?P<value>.+?)\s+"
        r"where\s+"
        r"(?P<wcol>[A-Za-z0-9_\- ./()]+?)\s+"
        r"(?P<op>contains|equals|=)\s+"
        r"(?P<wval>.+)$",
        flags=re.IGNORECASE,
    )
    m = p_a.match(text)
    if m:
        op = m.group("op").lower()
        if op == "=":
            op = "equals"
        return ParsedInstruction(
            target_column=m.group("target").strip(),
            target_value=m.group("value").strip().strip("\"'"),
            where_column=m.group("wcol").strip(),
            where_operator=op,
            where_value=m.group("wval").strip().strip("\"'"),
        )

    p_b = re.compile(
        r"(?:set|update|change)\s+"
        r"(?P<target>[A-Za-z0-9_\- ./()]+?)\s+"
        r"(?:to|=)\s+"
        r"(?P<value>.+?)\s+"
        r"for\s+class\s+(?P<class>[A-Za-z0-9_\-]+)"
        r"(?:\s+in\s+sheet\s+(?P<sheet>.+))?$",
        flags=re.IGNORECASE,
    )
    m = p_b.match(text)
    if m:
        sheet = m.group("sheet")
        return ParsedInstruction(
            target_column=m.group("target").strip(),
            target_value=m.group("value").strip().strip("\"'"),
            class_code=m.group("class").strip(),
            sheet_name=sheet.strip().strip("\"'") if sheet else None,
        )

    p_c = re.compile(
        r"(?:set|update|change)\s+"
        r"(?P<target>[A-Za-z0-9_\- ./()]+?)\s+"
        r"(?:to|=)\s+"
        r"(?P<value>.+?)\s+"
        r"in\s+sheet\s+(?P<sheet>.+)$",
        flags=re.IGNORECASE,
    )
    m = p_c.match(text)
    if m:
        return ParsedInstruction(
            target_column=m.group("target").strip(),
            target_value=m.group("value").strip().strip("\"'"),
            sheet_name=m.group("sheet").strip().strip("\"'"),
        )

    return None


def _is_repair_instruction(instruction: str) -> bool:
    """Recognize broad requests that ask the assistant to clean existing data."""
    text = _norm(instruction)
    repair_terms = {"align", "clean", "correct", "fix", "normalize", "rectify", "standardize"}
    data_terms = {"data", "issue", "issues", "record", "records", "row", "rows", "workbook"}
    return bool(_tokens(text).intersection(repair_terms) and _tokens(text).intersection(data_terms))


def _canonical_pressure(value: Any) -> str | None:
    text = re.sub(r"\s+", " ", str(value or "").strip().upper())
    match = re.fullmatch(r"(?:CLASS|CL|#)?\s*(\d{2,4})\s*#?", text)
    return f"CLASS {match.group(1)}" if match else None


def _canonical_facing(value: Any) -> str | None:
    text = _norm(value)
    if text in {"rf", "raised face", "raised-face"}:
        return "RF"
    if text in {"ff", "flat face", "flat-face"}:
        return "FF"
    if text in {"rtj", "ring type joint", "ring-type joint"}:
        return "RTJ"
    return None


def _canonical_end_connection(value: Any) -> str | None:
    text = _norm(value)
    aliases = {
        "butt weld": "BW", "buttweld": "BW", "bw": "BW",
        "socket weld": "SW", "socketweld": "SW", "sw": "SW",
        "threaded": "THR", "thread": "THR", "thr": "THR",
        "npt": "NPT", "rf": "RF", "ff": "FF", "rtj": "RTJ",
    }
    return aliases.get(text)


def _numeric_size(value: Any) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    try:
        return float(match.group(0)) if match else None
    except ValueError:
        return None


def _plan_safe_repairs_for_workbook(job, workbook: str) -> dict[str, Any]:
    """Plan only corrections whose intended value is unambiguous."""
    preview = build_preview(job, workbook)
    updates: list[dict[str, Any]] = []
    unresolved_count = 0
    source_rows = 0

    for sheet in preview.get("sheets") or []:
        headers = [str(header) for header in (sheet.get("headers") or [])]
        pressure_column = _resolve_name("PressureRating", headers)
        facing_column = _resolve_name("FlangeFacing", headers)
        end_column = _resolve_name("EndConnection", headers)
        size_from_column = _resolve_name("SizeFrom", headers)
        size_to_column = _resolve_name("SizeTo", headers)

        for row in sheet.get("rows") or []:
            cells = row.get("cells") or {}
            source = row.get("source") or {}
            if not (source.get("class_id") or source.get("component_id")):
                continue
            source_rows += 1

            def add_update(column_name: str, value: Any) -> None:
                previous_value = cells.get(column_name)
                if str(previous_value or "").strip() == str(value or "").strip():
                    return
                updates.append({
                    "workbook": workbook,
                    "sheet_name": sheet.get("name") or "",
                    "row_key": row.get("row_key") or "",
                    "column_name": column_name,
                    "value": value,
                    "previous_value": previous_value,
                    "source": {
                        "class_id": source.get("class_id"),
                        "class_code": source.get("class_code"),
                        "component_id": source.get("component_id"),
                    },
                })

            if pressure_column and str(cells.get(pressure_column) or "").strip():
                canonical = _canonical_pressure(cells.get(pressure_column))
                if canonical:
                    add_update(pressure_column, canonical)
                else:
                    unresolved_count += 1

            if facing_column and str(cells.get(facing_column) or "").strip():
                canonical = _canonical_facing(cells.get(facing_column))
                if canonical:
                    add_update(facing_column, canonical)
                else:
                    unresolved_count += 1

            if end_column and str(cells.get(end_column) or "").strip():
                canonical = _canonical_end_connection(cells.get(end_column))
                if canonical:
                    add_update(end_column, canonical)
                else:
                    unresolved_count += 1

            if size_from_column and size_to_column:
                size_from = _numeric_size(cells.get(size_from_column))
                size_to = _numeric_size(cells.get(size_to_column))
                if size_from is not None and size_to is not None and size_from > size_to:
                    add_update(size_from_column, cells.get(size_to_column))
                    add_update(size_to_column, cells.get(size_from_column))

            if len(updates) >= WORKBOOK_CHATBOT_CONFIG["safety"]["max_cell_updates"]:
                break

    return {
        "ok": True,
        "workbook": workbook,
        "operation": "repair",
        "planned_updates": updates,
        "planned_count": len(updates),
        "source_rows": source_rows,
        "retrieved_rows": int(preview.get("total_rows") or sum(
            len(sheet.get("rows") or []) for sheet in preview.get("sheets") or []
        )),
        "unresolved_count": unresolved_count,
        "retrieval_cache_hit": False,
    }


def _score_item(tokens: set[str], text_blob: str) -> int:
    if not tokens:
        return 0
    bag = _tokens(text_blob)
    return len(tokens.intersection(bag))


def _resolve_name(requested: str, options: list[str]) -> str | None:
    req = _norm(requested)
    if not req:
        return None

    norm_to_real = {_norm(o): o for o in options}
    if req in norm_to_real:
        return norm_to_real[req]

    req_t = _tokens(req)
    best = None
    best_score = 0
    for opt in options:
        sc = len(req_t.intersection(_tokens(opt)))
        if sc > best_score:
            best = opt
            best_score = sc
    return best if best_score > 0 else None


def _retrieval_index(job, workbook: str) -> dict[str, Any]:
    revision = get_workbook_revision(str(job.id), workbook)
    cache_key = f"{WORKBOOK_CHATBOT_CONFIG['cache']['index_prefix']}{job.id}:{workbook}:r{revision}"
    cached = _cache_get(cache_key)
    if cached:
        return {**cached, "_cache_hit": True}

    preview = build_preview(job, workbook)
    max_rows = WORKBOOK_CHATBOT_CONFIG["retrieval"]["max_rows_per_sheet"]
    index_rows: list[dict[str, Any]] = []
    headers_set: set[str] = set()

    for sheet in preview.get("sheets", []):
        sheet_name = sheet.get("name") or ""
        headers = sheet.get("headers") or []
        for h in headers:
            headers_set.add(str(h))

        for row in (sheet.get("rows") or [])[:max_rows]:
            cells = row.get("cells") or {}
            src = row.get("source") or {}
            blob_parts = [sheet_name, src.get("class_code") or "", row.get("row_key") or ""]
            for k, v in cells.items():
                blob_parts.append(str(k))
                blob_parts.append(str(v))
            index_rows.append({
                "sheet_name": sheet_name,
                "row_key": row.get("row_key") or "",
                "source": src,
                "cells": cells,
                "blob": " ".join(blob_parts),
            })

    built = {
        "preview": preview,
        "rows": index_rows,
        "headers": sorted(headers_set),
        "revision": revision,
        "_cache_hit": False,
    }
    _cache_set(cache_key, built)
    return built


def _match_row(parsed: ParsedInstruction, row: dict[str, Any], where_column_real: str | None) -> bool:
    src = row.get("source") or {}
    cells = row.get("cells") or {}

    if parsed.sheet_name:
        sheet_req = _norm(parsed.sheet_name)
        if sheet_req and sheet_req not in _norm(row.get("sheet_name") or ""):
            return False

    if parsed.class_code:
        cls = _norm(str(src.get("class_code") or ""))
        target_cls = _norm(parsed.class_code)
        # Accept exact or containment match to handle partial class references.
        if cls != target_cls and target_cls not in cls and cls not in target_cls:
            return False

    if not (parsed.where_column and parsed.where_operator and parsed.where_value):
        return True

    if not where_column_real:
        return False

    cell_text = _norm(str(cells.get(where_column_real) or ""))
    where_val = _norm(parsed.where_value)

    if parsed.where_operator == "contains":
        return where_val in cell_text
    return cell_text == where_val


def _plan_updates_for_workbook(job, workbook: str, instruction: str, *, resolved_target_value: str | None = None) -> dict[str, Any]:
    parsed = _parse_instruction(instruction)
    if not parsed:
        return {
            "ok": False,
            "error": "Unsupported instruction format. Use: set <column> to <value> where <column> contains <value>.",
            "help_examples": [
                "set MaterialGrade to ASTM A106 Gr.B where Description contains PIPE",
                "update CommodityCode = VLV-001 for class A1 in sheet PipingCommodityFilter",
                "set Notes to REVIEWED in sheet PipingMaterialsClassData",
            ],
        }

    idx = _retrieval_index(job, workbook)
    headers = idx["headers"]
    target_column_real = _resolve_name(parsed.target_column, headers)
    where_column_real = _resolve_name(parsed.where_column or "", headers) if parsed.where_column else None

    if not target_column_real:
        return {
            "ok": False,
            "error": f"Could not resolve target column '{parsed.target_column}'.",
            "retrieved_columns": headers[:50],
        }

    if parsed.where_column and not where_column_real:
        return {
            "ok": False,
            "error": f"Could not resolve where column '{parsed.where_column}'.",
            "retrieved_columns": headers[:50],
        }

    q_tokens = _tokens(instruction)
    scored = []
    for row in idx["rows"]:
        sc = _score_item(q_tokens, row["blob"])
        if sc > 0:
            scored.append((sc, row))
    scored.sort(key=lambda x: x[0], reverse=True)

    top_k = WORKBOOK_CHATBOT_CONFIG["retrieval"]["top_k"]
    candidate_rows = [r for _, r in scored[:top_k]]

    if not candidate_rows:
        candidate_rows = idx["rows"]

    updates = []
    touched_keys = set()

    def collect_updates(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        planned = []
        for row in rows:
            if not _match_row(parsed, row, where_column_real):
                continue
            cells = row.get("cells") or {}
            source = row.get("source") or {}
            row_key = row["row_key"]
            sheet_name = row["sheet_name"]
            dedupe = f"{sheet_name}::{row_key}::{target_column_real}"
            if dedupe in touched_keys:
                continue
            touched_keys.add(dedupe)
            planned.append({
                "workbook": workbook,
                "sheet_name": sheet_name,
                "row_key": row_key,
                "column_name": target_column_real,
                "value": resolved_target_value if resolved_target_value is not None else parsed.target_value,
                "previous_value": cells.get(target_column_real),
                "source": {
                    "class_id": source.get("class_id"),
                    "class_code": source.get("class_code"),
                    "component_id": source.get("component_id"),
                },
            })
            if len(planned) + len(updates) >= WORKBOOK_CHATBOT_CONFIG["safety"]["max_cell_updates"]:
                break
        return planned

    # Pass 1: top-k lexical rows.
    updates.extend(collect_updates(candidate_rows))

    # Pass 2 fallback: full-sheet scan if top-k returned zero matches.
    if not updates and candidate_rows is not idx["rows"]:
        updates.extend(collect_updates(idx["rows"]))

    return {
        "ok": True,
        "workbook": workbook,
        "parsed": {
            "target_column": target_column_real,
            "target_value": parsed.target_value,
            "where_column": where_column_real,
            "where_operator": parsed.where_operator,
            "where_value": parsed.where_value,
            "class_code": parsed.class_code,
            "sheet_name": parsed.sheet_name,
        },
        "planned_updates": updates,
        "planned_count": len(updates),
        "retrieved_rows": len(candidate_rows),
        "retrieval_cache_hit": bool(idx.get("_cache_hit")),
    }


def apply_workbook_chat_instruction(
    *,
    job,
    instruction: str,
    workbook_scope: str,
    active_workbook: str,
    user=None,
    preview_only: bool = False,
) -> dict[str, Any]:
    if not WORKBOOK_CHATBOT_CONFIG["enabled"]:
        return {"ok": False, "error": "Workbook chatbot is disabled by configuration."}

    instruction = (instruction or "").strip()
    if not instruction:
        return {"ok": False, "error": "instruction is required"}

    if len(instruction) > WORKBOOK_CHATBOT_CONFIG["safety"]["max_instruction_chars"]:
        return {
            "ok": False,
            "error": f"instruction exceeds {WORKBOOK_CHATBOT_CONFIG['safety']['max_instruction_chars']} characters",
        }

    workbooks = _workbook_for_instruction(instruction, workbook_scope, active_workbook)
    parsed = _parse_instruction(instruction)
    repair_requested = parsed is None and _is_repair_instruction(instruction)

    if (
        parsed
        and parsed.sheet_name
        and _norm(workbook_scope) == "auto"
        and set(workbooks) != {WORKBOOK_SPEC, WORKBOOK_CAT}
    ):
        workbooks = [WORKBOOK_SPEC, WORKBOOK_CAT]

    # A plan is valid only for the editable workbook revision it was created
    # from.  Applies are intentionally never cached: they are state changes.
    workbook_revisions = {
        wb: get_workbook_revision(str(job.id), wb)
        for wb in workbooks
    }
    cache_basis = f"{job.id}:{','.join(workbooks)}:{workbook_revisions}:{instruction}:preview=1"
    result_key = WORKBOOK_CHATBOT_CONFIG["cache"]["result_prefix"] + hashlib.sha256(cache_basis.encode("utf-8")).hexdigest()
    if preview_only:
        cached = _cache_get(result_key)
        if cached:
            runtime = _rag_cag_runtime(
                cache_hit=True,
                index_cache_hits=0,
                workbooks=workbooks,
                instruction=instruction,
                evidence_count=len(cached.get("document_evidence") or []),
                auto_value_used=bool(cached.get("auto_value", {}).get("used")),
            )
            return {
                **cached,
                "cache_hit": True,
                "runtime": runtime,
                "document_context": _uploaded_document_context(job),
            }

    document_evidence = _retrieve_document_evidence(job, instruction, parsed)
    resolved_target_value = None
    auto_value_used = False
    auto_value_requested = False
    if parsed and _looks_like_auto_value(parsed.target_value):
        auto_value_requested = True
        resolved_target_value = _derive_value_from_evidence(parsed.target_column, document_evidence)
        if resolved_target_value:
            auto_value_used = True

    if auto_value_requested and not resolved_target_value:
        runtime = _rag_cag_runtime(
            cache_hit=False,
            index_cache_hits=0,
            workbooks=workbooks,
            instruction=instruction,
            evidence_count=len(document_evidence),
            auto_value_used=False,
        )
        guidance = (
            "Could not infer a concrete value from uploaded document evidence. "
            "Try an explicit value (e.g. ASTM A106 Gr.B) or a valid page within the document."
        )
        return {
            "ok": True,
            "preview_only": True,
            "instruction": instruction,
            "workbooks": workbooks,
            "workbook_revisions": workbook_revisions,
            "applied_count": 0,
            "planned_count": 0,
            "workbook_results": [],
            "runtime": runtime,
            "document_context": _uploaded_document_context(job),
            "document_evidence": document_evidence,
            "auto_value": {
                "used": False,
                "resolved": None,
                "requested": True,
            },
            "confidence": {
                "score": 0.15,
                "label": "low",
                "drivers": {
                    "planned_count": 0,
                    "retrieved_rows": 0,
                    "evidence_count": len(document_evidence),
                    "auto_value_used": False,
                },
            },
            "warning": guidance,
        }

    workbook_results = []
    all_updates = []
    index_cache_hits = 0
    for wb in workbooks:
        res = (
            _plan_safe_repairs_for_workbook(job, wb)
            if repair_requested
            else _plan_updates_for_workbook(job, wb, instruction, resolved_target_value=resolved_target_value)
        )
        workbook_results.append(res)
        if res.get("retrieval_cache_hit"):
            index_cache_hits += 1
        if res.get("ok"):
            all_updates.extend(res.get("planned_updates") or [])

    max_updates = WORKBOOK_CHATBOT_CONFIG["safety"]["max_cell_updates"]
    if len(all_updates) > max_updates:
        all_updates = all_updates[:max_updates]

    if not all_updates:
        runtime = _rag_cag_runtime(
            cache_hit=False,
            index_cache_hits=index_cache_hits,
            workbooks=workbooks,
            instruction=instruction,
            evidence_count=len(document_evidence),
            auto_value_used=auto_value_used,
        )
        if repair_requested:
            unresolved_count = sum(int(result.get("unresolved_count") or 0) for result in workbook_results)
            source_rows = sum(int(result.get("source_rows") or 0) for result in workbook_results)
            if source_rows == 0:
                warning = (
                    "This job has no extracted class or component records to repair. "
                    "The visible rows are workbook template reference rows and are intentionally not auto-modified."
                )
                tip = "Re-run extraction on the source document, then ask me to rectify and align the extracted records."
            else:
                warning = (
                    "No safe automatic corrections were found. "
                    "Missing or ambiguous values need an explicit value or document reference before they can be changed."
                )
                tip = "Try: set <column> to <value> where <column> contains <text>, or include a source page."
        else:
            unresolved_count = 0
            warning = "No matching rows found for this instruction."
            tip = "Use a sheet name present in workbook and ensure where-conditions match existing rows."
        return {
            "ok": True,
            "preview_only": True,
            "instruction": instruction,
            "operation": "repair" if repair_requested else "edit",
            "workbooks": workbooks,
            "workbook_revisions": workbook_revisions,
            "applied_count": 0,
            "planned_count": 0,
            "warning": warning,
            "error": warning,
            "unresolved_count": unresolved_count,
            "source_rows": source_rows if repair_requested else 0,
            "workbook_results": workbook_results,
            "runtime": runtime,
            "document_context": _uploaded_document_context(job),
            "document_evidence": document_evidence,
            "auto_value": {
                "used": auto_value_used,
                "resolved": resolved_target_value,
                "requested": auto_value_requested,
            },
            "suggestions": {
                "try_workbook_scope": "both",
                "tip": tip,
            },
        }

    for i, wb_res in enumerate(workbook_results):
        if not wb_res.get("ok"):
            continue
        workbook_results[i] = {
            **wb_res,
            "confidence": _compute_confidence(
                planned_count=int(wb_res.get("planned_count") or 0),
                retrieved_rows=int(wb_res.get("retrieved_rows") or 0),
                evidence_count=len(document_evidence),
                auto_value_used=auto_value_used,
            ),
        }

    overall_confidence = _compute_confidence(
        planned_count=len(all_updates),
        retrieved_rows=sum(int(r.get("retrieved_rows") or 0) for r in workbook_results if r.get("ok")),
        evidence_count=len(document_evidence),
        auto_value_used=auto_value_used,
    )

    runtime = _rag_cag_runtime(
        cache_hit=False,
        index_cache_hits=index_cache_hits,
        workbooks=workbooks,
        instruction=instruction,
        evidence_count=len(document_evidence),
        auto_value_used=auto_value_used,
    )

    if preview_only:
        response = {
            "ok": True,
            "preview_only": True,
            "instruction": instruction,
            "operation": "repair" if repair_requested else "edit",
            "workbooks": workbooks,
            "workbook_revisions": workbook_revisions,
            "applied_count": 0,
            "planned_count": len(all_updates),
            "workbook_results": workbook_results,
            "runtime": runtime,
            "document_context": _uploaded_document_context(job),
            "document_evidence": document_evidence,
            "auto_value": {
                "used": auto_value_used,
                "resolved": resolved_target_value,
                "requested": auto_value_requested,
            },
            "confidence": overall_confidence,
        }
        _cache_set(result_key, response)
        return response

    evidence_pages = sorted({
        int(item["page"])
        for item in document_evidence
        if item.get("page") is not None
    })
    approved_at = timezone.now()
    cells_with_provenance = []
    for update in all_updates:
        source = update.get("source") or {}
        cells_with_provenance.append({
            **update,
            "source_class_id": source.get("class_id"),
            "source_component_id": source.get("component_id"),
            "edit_origin": "chatbot",
            "evidence_pages": evidence_pages,
            "auto_value_used": auto_value_used,
            "approved_at": approved_at,
        })

    save_result = batch_save_cells(job=job, cells=cells_with_provenance, user=user)

    response = {
        "ok": True,
        "preview_only": False,
        "instruction": instruction,
        "operation": "repair" if repair_requested else "edit",
        "workbooks": workbooks,
        "workbook_revisions": workbook_revisions,
        "applied_count": len(all_updates),
        "planned_count": len(all_updates),
        "save_result": save_result,
        "workbook_results": workbook_results,
        "runtime": runtime,
        "document_context": _uploaded_document_context(job),
        "document_evidence": document_evidence,
        "auto_value": {
            "used": auto_value_used,
            "resolved": resolved_target_value,
            "requested": auto_value_requested,
        },
        "confidence": overall_confidence,
    }
    return response
