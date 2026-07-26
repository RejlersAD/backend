"""
Tag Naming & Acronym Check Service
=====================================
AI-powered + deterministic checks for P&ID tag naming convention violations.

Deterministic checks (no AI, instant):
  NAM-001  Tag uses wrong separator (space / underscore / slash / dot)
  NAM-002  Function letter code not in ISA-5.1 vocabulary
  NAM-003  Incomplete tag — function code present but no sequential number
  NAM-004  Inconsistent area-prefix usage across the drawing

AI vision check (Gemini primary → OpenAI fallback):
  NAM-AI   Visual scan: naming errors visible on drawing, not catchable by OCR text alone

Config: backend/domain_knowledge/pid_verification/naming_check_config.json
All thresholds, prompts, model names and severity levels are soft-coded there.
"""
import base64
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Config path ───────────────────────────────────────────────────────────────
_CONFIG_PATH = (
    Path(__file__).resolve().parents[3]
    / "domain_knowledge" / "pid_verification" / "naming_check_config.json"
)

_CACHED_CONFIG: Optional[Dict[str, Any]] = None


def _load_config() -> Dict[str, Any]:
    """Load and cache the naming check config JSON. Thread-safe for reads."""
    global _CACHED_CONFIG
    if _CACHED_CONFIG is None:
        try:
            _CACHED_CONFIG = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
            logger.info("[NamingCheck] Config loaded from %s", _CONFIG_PATH)
        except Exception as exc:
            logger.warning("[NamingCheck] config load failed (%s) — using defaults", exc)
            _CACHED_CONFIG = _default_config()
    return _CACHED_CONFIG


def reload_config() -> None:
    """Force reload of the config on next call (call after a config file edit)."""
    global _CACHED_CONFIG
    _CACHED_CONFIG = None


def _default_config() -> Dict[str, Any]:
    """Minimal fallback config when the JSON file is missing."""
    return {
        "enabled": True,
        "ai_provider": "gemini",
        "run_deterministic": True,
        "run_ai_vision": True,
        "valid_isa51_prefixes": {
            "FI": "Flow Indicator", "FIC": "Flow Indicating Controller",
            "FT": "Flow Transmitter", "PI": "Pressure Indicator",
            "PIC": "Pressure Indicating Controller", "PT": "Pressure Transmitter",
            "TI": "Temperature Indicator", "TT": "Temperature Transmitter",
            "LI": "Level Indicator", "LT": "Level Transmitter",
            "AT": "Analyzer Transmitter", "HV": "Hand Valve",
            "XV": "On/Off Valve", "SDV": "Shut-Down Valve",
            "BDV": "Blowdown Valve", "PSV": "Pressure Safety Valve",
        },
        "valid_equipment_codes": {
            "P": "Pump", "V": "Vessel", "E": "Exchanger",
            "K": "Compressor", "T": "Tank",
        },
        "tag_format_rules": {
            "expected_separator": "-",
            "forbidden_separators": [" ", "_", "/", "."],
        },
        "severity_map": {
            "wrong_separator": "major",
            "unknown_acronym": "major",
            "incomplete_tag": "minor",
            "inconsistent_area_prefix": "minor",
            "ai_detected": "major",
        },
        "ai_model": "gemini-2.0-flash",
        "ai_fallback_model": "gpt-4o",
        "max_tokens": 8000,
        "temperature": 0.0,
        "timeout_seconds": 120,
        "ai_system_prompt": (
            "You are an expert P&ID reviewer specialising in ISA-5.1 instrument tag naming "
            "conventions. Find naming errors only."
        ),
        "ai_prompt_template": (
            "Check this P&ID for tag naming and acronym violations.\n"
            "OCR tags for reference: {ocr_tags}\n"
            "Return JSON: {\"naming_issues\": [{\"tag_found\":\"...\",\"issue_type\":\"...\","
            "\"description\":\"...\",\"suggested_fix\":\"...\",\"severity\":\"major\","
            "\"location_hint\":\"...\"}], \"summary\": \"...\"}"
        ),
    }


# ── Compiled regex patterns ───────────────────────────────────────────────────
# Loose match: any alphabetic prefix + separator (space/dash/underscore) + number
_LOOSE_TAG_RE = re.compile(
    r'(?<![A-Z0-9])([A-Z]{2,5})([ _/\.])(\d{3,5}[A-Z]?)(?![A-Z0-9])',
    re.IGNORECASE,
)

# Fused tag: alphabetic prefix directly joined to a number (no separator at all)
_FUSED_TAG_RE = re.compile(
    r'(?<![A-Z0-9\-])([A-Z]{2,5})(\d{3,5})(?![A-Z0-9\-])',
    re.IGNORECASE,
)


# ── Deterministic checks ──────────────────────────────────────────────────────

def run_deterministic_checks(tags: List[str], raw_text: str, config: Dict[str, Any]) -> List[Dict]:
    """
    Apply regex + vocabulary checks to OCR-extracted tags and raw text.
    Returns a list of naming issue dicts (source='deterministic').
    """
    valid_prefixes = set(config.get("valid_isa51_prefixes", {}).keys())
    sev_map        = config.get("severity_map", {})
    rules          = config.get("tag_format_rules", {})
    forbidden_seps = rules.get("forbidden_separators", [" ", "_", "/", "."])

    issues: List[Dict] = []
    seen_dedup: set = set()

    # ── Area-prefix consistency tracking ─────────────────────────────────────
    with_area  = 0
    without_area = 0

    for raw_tag in tags:
        tag = raw_tag.strip().upper()
        if not tag:
            continue
        parts = tag.split("-")

        # Determine function-letter prefix
        if len(parts) >= 3 and parts[0].isdigit():
            # Format: AREA-PREFIX-NUMBER (e.g. 16-PI-3610)
            prefix = parts[1]
            with_area += 1
        elif len(parts) == 2 and not parts[0].isdigit():
            # Format: PREFIX-NUMBER (e.g. PI-3610)
            prefix = parts[0]
            without_area += 1
        elif len(parts) == 1:
            # Might be PREFIX only (NAM-003: incomplete) or malformed
            prefix = parts[0]
            # Only flag if it looks like a function code (all letters, 2-5 chars)
            if re.fullmatch(r'[A-Z]{2,5}', prefix) and prefix in valid_prefixes:
                key = ("NAM-003", prefix)
                if key not in seen_dedup:
                    seen_dedup.add(key)
                    issues.append({
                        "rule_id":        "NAM-003",
                        "tag_found":      raw_tag,
                        "issue_type":     "incomplete_tag",
                        "description":    f"'{prefix}' appears as a standalone function code with no sequential number",
                        "suggested_fix":  f"{prefix}-XXXX (add the tag number)",
                        "severity":       sev_map.get("incomplete_tag", "minor"),
                        "location_hint":  "",
                        "source":         "deterministic",
                    })
            continue
        else:
            prefix = parts[0] if parts else ""

        # NAM-002: Unknown / non-standard acronym
        if prefix and len(prefix) >= 2 and not prefix.isdigit():
            # Skip single-letter equipment codes (handled separately)
            if prefix not in valid_prefixes and not (
                len(prefix) == 1 and prefix in config.get("valid_equipment_codes", {})
            ):
                key = ("NAM-002", prefix)
                if key not in seen_dedup:
                    seen_dedup.add(key)
                    issues.append({
                        "rule_id":        "NAM-002",
                        "tag_found":      raw_tag,
                        "issue_type":     "unknown_acronym",
                        "description":    (
                            f"'{prefix}' is not a recognised ISA-5.1 instrument or valve "
                            f"function code. Possible typo or non-standard abbreviation."
                        ),
                        "suggested_fix":  "Verify against ISA-5.1 or project legend sheet",
                        "severity":       sev_map.get("unknown_acronym", "major"),
                        "location_hint":  "",
                        "source":         "deterministic",
                    })

    # ── NAM-001: Wrong separator detected in raw OCR text ────────────────────
    for m in _LOOSE_TAG_RE.finditer(raw_text):
        func_code = m.group(1).upper()
        sep       = m.group(2)
        number    = m.group(3)

        if sep not in forbidden_seps:
            continue
        if func_code not in valid_prefixes:
            continue

        key = ("NAM-001", f"{func_code}{sep}{number}")
        if key not in seen_dedup:
            sep_name = {"  ": "space", " ": "space", "_": "underscore",
                        "/": "slash", ".": "dot"}.get(sep, repr(sep))
            seen_dedup.add(key)
            issues.append({
                "rule_id":        "NAM-001",
                "tag_found":      f"{func_code}{sep}{number}",
                "issue_type":     "wrong_separator",
                "description":    (
                    f"Tag uses a {sep_name} separator instead of a dash "
                    f"(ISA-5.1 requires FUNC-NUMBER format, e.g. {func_code}-{number})"
                ),
                "suggested_fix":  f"{func_code}-{number}",
                "severity":       sev_map.get("wrong_separator", "major"),
                "location_hint":  "",
                "source":         "deterministic",
            })

    # ── NAM-001b: Fused tags (no separator at all) ───────────────────────────
    for m in _FUSED_TAG_RE.finditer(raw_text):
        func_code = m.group(1).upper()
        number    = m.group(2)
        if func_code not in valid_prefixes:
            continue
        key = ("NAM-001b", f"{func_code}{number}")
        if key not in seen_dedup:
            seen_dedup.add(key)
            issues.append({
                "rule_id":        "NAM-001",
                "tag_found":      f"{func_code}{number}",
                "issue_type":     "missing_separator",
                "description":    (
                    f"Tag '{func_code}{number}' is missing the dash separator "
                    f"between function code and number"
                ),
                "suggested_fix":  f"{func_code}-{number}",
                "severity":       sev_map.get("wrong_separator", "major"),
                "location_hint":  "",
                "source":         "deterministic",
            })

    # ── NAM-004: Inconsistent area-prefix usage ───────────────────────────────
    if with_area > 0 and without_area > 0 and (with_area + without_area) >= 4:
        issues.append({
            "rule_id":        "NAM-004",
            "tag_found":      "Multiple tags",
            "issue_type":     "inconsistent_area_prefix",
            "description":    (
                f"{with_area} tag(s) use area-prefix format (NN-PREFIX-NUM) "
                f"while {without_area} tag(s) do not — drawing uses mixed naming conventions"
            ),
            "suggested_fix":  "Standardise all tags to one format per project convention",
            "severity":       sev_map.get("inconsistent_area_prefix", "minor"),
            "location_hint":  "",
            "source":         "deterministic",
        })

    return issues


# ── Image rendering ───────────────────────────────────────────────────────────

def _render_page_to_b64(file_path: str, page_index: int = 0) -> Optional[str]:
    """Render a PDF or image page to base64-encoded PNG for AI vision."""
    try:
        import fitz  # PyMuPDF
        ext = Path(file_path).suffix.lower().lstrip(".")
        if ext == "pdf":
            doc = fitz.open(file_path)
            if page_index >= len(doc):
                page_index = 0
            page = doc[page_index]
            mat = fitz.Matrix(2.0, 2.0)  # approx 150 dpi
            pix = page.get_pixmap(matrix=mat, alpha=False)
            png_bytes = pix.tobytes("png")
            doc.close()
        else:
            # For raster images, load as-is (PIL converts to PNG if needed)
            with open(file_path, "rb") as fh:
                raw = fh.read()
            if ext == "png":
                png_bytes = raw
            else:
                from PIL import Image
                import io as _io
                img = Image.open(_io.BytesIO(raw)).convert("RGB")
                buf = _io.BytesIO()
                img.save(buf, format="PNG")
                png_bytes = buf.getvalue()
        return base64.b64encode(png_bytes).decode("ascii")
    except Exception as exc:
        logger.warning("[NamingCheck] Page render failed: %s", exc)
        return None


# ── AI provider helpers ───────────────────────────────────────────────────────

def _call_gemini(
    system_prompt: str,
    user_prompt:   str,
    image_b64:     str,
    config:        Dict[str, Any],
) -> str:
    """Call Gemini vision. Returns response text or '' on failure."""
    try:
        from google import genai
        from google.genai import types as _gtypes

        api_key = (
            os.getenv("GEMINI_API_KEY")
            or os.getenv("GOOGLE_GENERATIVEAI_API_KEY")
        )
        if not api_key:
            logger.warning("[NamingCheck] GEMINI_API_KEY not set — Gemini skipped")
            return ""

        client = genai.Client(api_key=api_key)
        model  = config.get("ai_model", "gemini-2.0-flash")

        parts = [
            user_prompt,
            _gtypes.Part.from_bytes(
                data=base64.b64decode(image_b64),
                mime_type="image/png",
            ),
        ]
        cfg = _gtypes.GenerateContentConfig(
            system_instruction=system_prompt or None,
            max_output_tokens=min(config.get("max_tokens", 8000), 65536),
            temperature=float(config.get("temperature", 0.0)),
            seed=42,
        )
        resp = client.models.generate_content(model=model, contents=parts, config=cfg)
        text = (resp.text or "").strip()
        logger.info("[NamingCheck] Gemini returned %d chars", len(text))
        return text
    except ImportError:
        logger.warning("[NamingCheck] google-genai not installed — Gemini skipped")
        return ""
    except Exception as exc:
        logger.warning("[NamingCheck] Gemini call failed: %s", exc)
        return ""


def _call_openai(
    system_prompt: str,
    user_prompt:   str,
    image_b64:     str,
    config:        Dict[str, Any],
) -> str:
    """Call OpenAI vision. Returns response text or '' on failure."""
    try:
        from openai import OpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            logger.warning("[NamingCheck] OPENAI_API_KEY not set — OpenAI skipped")
            return ""

        client = OpenAI(api_key=api_key, timeout=float(config.get("timeout_seconds", 120)))
        model  = config.get("ai_fallback_model", "gpt-4o")

        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {
                        "url":    f"data:image/png;base64,{image_b64}",
                        "detail": "high",
                    }},
                ]},
            ],
            max_tokens=int(config.get("max_tokens", 8000)),
            temperature=float(config.get("temperature", 0.0)),
        )
        text = (resp.choices[0].message.content or "").strip()
        logger.info("[NamingCheck] OpenAI returned %d chars", len(text))
        return text
    except ImportError:
        logger.warning("[NamingCheck] openai not installed — OpenAI skipped")
        return ""
    except Exception as exc:
        logger.warning("[NamingCheck] OpenAI call failed: %s", exc)
        return ""


# ── AI vision check ───────────────────────────────────────────────────────────

def run_ai_check(
    file_path:  str,
    page_index: int,
    tags:       List[str],
    config:     Dict[str, Any],
) -> List[Dict]:
    """
    Run AI vision pass on the drawing page.
    Returns list of AI-detected naming issue dicts (source='ai_vision').
    Falls back gracefully if AI is unavailable — returns [].
    """
    image_b64 = _render_page_to_b64(file_path, page_index)
    if not image_b64:
        logger.warning("[NamingCheck] Could not render drawing page — AI check skipped")
        return []

    system_prompt     = config.get("ai_system_prompt", "")
    prompt_template   = config.get("ai_prompt_template", "")
    ocr_tags_text     = ", ".join(tags[:200]) if tags else "(none extracted by OCR)"
    user_prompt       = prompt_template.replace("{ocr_tags}", ocr_tags_text)

    provider = config.get("ai_provider", "gemini")
    raw      = ""

    if provider == "gemini":
        raw = _call_gemini(system_prompt, user_prompt, image_b64, config)
    if not raw:
        raw = _call_openai(system_prompt, user_prompt, image_b64, config)

    if not raw:
        return []

    # Parse JSON response — be forgiving of markdown fences
    try:
        # Strip ```json ... ``` fences if present
        clean = re.sub(r'^```[a-z]*\s*', '', raw.strip(), flags=re.IGNORECASE)
        clean = re.sub(r'\s*```$', '', clean.strip())
        # Extract first {...} block
        m = re.search(r'\{.*\}', clean, re.DOTALL)
        payload_str = m.group() if m else clean
        data        = json.loads(payload_str)
        ai_issues   = data.get("naming_issues", [])
    except Exception as parse_exc:
        logger.warning("[NamingCheck] AI JSON parse failed (%s) — raw=%s...", parse_exc, raw[:200])
        return []

    sev_map = config.get("severity_map", {})
    result: List[Dict] = []
    for iss in ai_issues:
        if not isinstance(iss, dict):
            continue
        result.append({
            "rule_id":        "NAM-AI",
            "tag_found":       str(iss.get("tag_found",     "")),
            "issue_type":      str(iss.get("issue_type",    "ai_detected")),
            "description":     str(iss.get("description",   "")),
            "suggested_fix":   str(iss.get("suggested_fix", "")),
            "severity":        str(iss.get("severity", sev_map.get("ai_detected", "major"))),
            "location_hint":   str(iss.get("location_hint", "")),
            "source":          "ai_vision",
        })

    logger.info("[NamingCheck] AI returned %d naming issues", len(result))
    return result


# ── Main entry point ──────────────────────────────────────────────────────────

def check_naming_conventions(
    file_path:   str,
    page_index:  int,
    tags:        List[str],
    raw_text:    str,
    run_ai:      bool = True,
) -> Dict[str, Any]:
    """
    Run the full naming & acronym convention check for one drawing page.

    Args:
        file_path:   Absolute path to the original drawing file (PDF or image).
        page_index:  Zero-based page number.
        tags:        Tag strings already extracted by the OCR extraction service.
        raw_text:    Full raw OCR text from the page.
        run_ai:      If False, only deterministic checks are run (no API cost).

    Returns:
        {
          "naming_issues": [ { rule_id, tag_found, issue_type, description,
                                suggested_fix, severity, location_hint, source }, ... ],
          "total":         int,
          "by_severity":   { "major": N, "minor": N, ... },
          "ai_used":       bool,
          "config_path":   str,
        }
    """
    config = _load_config()

    if not config.get("enabled", True):
        logger.info("[NamingCheck] Feature disabled in config — returning empty result")
        return {
            "naming_issues": [],
            "total":         0,
            "by_severity":   {},
            "ai_used":       False,
            "config_path":   str(_CONFIG_PATH),
        }

    issues: List[Dict] = []

    # 1. Deterministic checks
    if config.get("run_deterministic", True):
        det_issues = run_deterministic_checks(tags, raw_text, config)
        issues.extend(det_issues)
        logger.info("[NamingCheck] Deterministic found %d issues", len(det_issues))

    # 2. AI vision check
    ai_used  = False
    do_ai    = run_ai and config.get("run_ai_vision", True)
    if do_ai:
        # Deduplicate against deterministic findings before calling AI
        existing_tags = {i["tag_found"] for i in issues if i["rule_id"] != "NAM-004"}
        ai_issues     = run_ai_check(file_path, page_index, tags, config)
        # Merge: keep AI issue only when it doesn't duplicate a deterministic finding
        for ai_iss in ai_issues:
            if ai_iss["tag_found"] not in existing_tags:
                issues.append(ai_iss)
                existing_tags.add(ai_iss["tag_found"])
        ai_used = True

    # Tally by severity
    by_severity: Dict[str, int] = {}
    for iss in issues:
        sev = iss.get("severity", "minor")
        by_severity[sev] = by_severity.get(sev, 0) + 1

    return {
        "naming_issues": issues,
        "total":         len(issues),
        "by_severity":   by_severity,
        "ai_used":       ai_used,
        "config_path":   str(_CONFIG_PATH),
    }
