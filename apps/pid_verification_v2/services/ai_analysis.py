"""
AI Analysis Services — BYOK (Bring Your Own Key)
==================================================
Deep analysis modes using user-provided API keys for OpenAI and Claude.

Analysis Modes:
  - standard: Default rule-based engine (no AI, free)
  - enhanced_openai: GPT-4o enhanced analysis
  - deep_claude: Claude 3.5 Sonnet deep analysis
  - hybrid: Both OpenAI + Claude (cross-validation)

Security: API keys are NEVER persisted — passed in-memory only from frontend sessionStorage.
"""
import logging
import json
import re
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# OPENAI ANALYSIS (GPT-4o)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_openai_analysis(
    drawing_data: dict,
    api_key: str,
    model: str = "gpt-4o",
    temperature: float = 0.2,
) -> Dict[str, Any]:
    """
    Enhanced P&ID analysis using OpenAI GPT-4o.

    Text-only (unlike run_claude_analysis — see module docstring). Kept as-is
    since Priority 1 of this rework scoped the real-Vision upgrade to Claude
    only; OpenAI's `enhanced_openai` mode is unaffected.

    Args:
        drawing_data: Extracted drawing data (instruments, valves, lines, etc.)
        api_key: User-provided OpenAI API key (format: sk-...)
        model: OpenAI model name (default: gpt-4o)
        temperature: Sampling temperature (0-1, lower = more deterministic)

    Returns:
        {'findings': [...], 'symbols': []} — symbols always empty (text-only)

    Raises:
        ValueError: If API key is invalid
        RuntimeError: If OpenAI API call fails
    """
    if not api_key or not api_key.startswith('sk-'):
        raise ValueError("Invalid OpenAI API key format")
    
    try:
        import openai
        
        # Initialize client with user's key
        client = openai.OpenAI(api_key=api_key)
        
        # Build structured prompt
        prompt = _build_openai_prompt(drawing_data)
        
        logger.info("[OpenAI] Sending request to %s (temp=%.2f)", model, temperature)
        
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": """You are an expert P&ID verification engineer with 20+ years of experience in Oil & Gas projects.
Analyze the provided P&ID drawing data and identify potential issues, inconsistencies, and recommendations.

Focus on:
1. Instrument tagging inconsistencies (ISA S5.1 standard)
2. Valve sizing and specification issues
3. Line routing and connectivity problems
4. Missing or incorrect safety instrumentation
5. Control loop completeness
6. Redundancy and fail-safe design

Return findings in JSON array format with keys: category, severity, tag_number, issue_observed, recommendation."""
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            temperature=temperature,
            max_tokens=4000,
            response_format={"type": "json_object"}
        )
        
        # Parse response
        content = response.choices[0].message.content
        findings_data = json.loads(content)
        
        # Normalize findings
        findings = findings_data.get('findings', [])
        normalized = []
        
        for idx, finding in enumerate(findings, start=1):
            normalized.append({
                'category': finding.get('category', 'General'),
                'severity': finding.get('severity', 'medium').lower(),
                'tag_number': finding.get('tag_number', 'N/A'),
                'issue_observed': finding.get('issue_observed', ''),
                'recommendation': finding.get('recommendation', ''),
                'rule_id': f"OPENAI_{idx:03d}",
                'evidence': {
                    'ai_model': model,
                    'confidence': finding.get('confidence', 0.8),
                    'source': 'OpenAI GPT-4o'
                }
            })
        
        logger.info("[OpenAI] Received %d findings", len(normalized))
        return {'findings': normalized, 'symbols': []}

    except ImportError:
        raise RuntimeError("OpenAI package not installed. Run: pip install openai")
    except openai.AuthenticationError:
        raise RuntimeError("OpenAI API key authentication failed. Please verify your key.")
    except openai.RateLimitError:
        raise RuntimeError("OpenAI API rate limit exceeded. Please try again later.")
    except Exception as e:
        logger.error("[OpenAI] Analysis failed: %s", str(e), exc_info=True)
        raise RuntimeError(f"OpenAI analysis failed: {str(e)}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLAUDE ANALYSIS (Claude 3.5 Sonnet)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_VISION_SYSTEM_PROMPT = (
    "You are a world-class P&ID verification specialist with deep expertise in "
    "process engineering, instrumentation, and safety systems. You are looking at "
    "the ACTUAL rendered page image of a P&ID drawing — not just a text summary — "
    "so base your findings on what you can see: symbol placement, connectivity, "
    "annotations, and anything the OCR-extracted text below may have missed or "
    "misread. This may be a scanned drawing; image quality, contrast, or "
    "resolution may be low — look carefully rather than skipping unclear areas.\n\n"
    "Perform comprehensive analysis focusing on:\n"
    "1. ISA standards compliance (S5.1, S18.1, S84)\n"
    "2. Safety Instrumented Systems (SIS) design\n"
    "3. Process hazard analysis\n"
    "4. Material selection and compatibility\n"
    "5. Flow assurance and hydraulics\n"
    "6. Control system architecture\n\n"
    "Provide detailed, actionable findings with severity assessment."
)

_VISION_RESPONSE_INSTRUCTIONS_WITH_FINDINGS = """Return your analysis as a single JSON object with exactly two keys, "findings" and "symbols":
{
  "findings": [
    {"category": "...", "severity": "critical|high|medium|low", "tag_number": "...", "issue_observed": "...", "recommendation": "...", "confidence": 0.0-1.0}
  ],
  "symbols": [
    {"symbol_type": "<EXACT label from a matching reference picture>", "location": "top-left|top|top-right|middle-left|center|middle-right|bottom-left|bottom|bottom-right", "confidence": "high|medium|low"}
  ]
}
If no reference symbol pictures were provided above, or none match, return an empty "symbols" array.
Return ONLY the JSON object — no prose, no markdown fences."""

_VISION_RESPONSE_INSTRUCTIONS_SYMBOLS_ONLY = """Return your analysis as a single JSON object with exactly one key, "symbols":
{
  "symbols": [
    {"symbol_type": "<EXACT label from a matching reference picture>", "location": "top-left|top|top-right|middle-left|center|middle-right|bottom-left|bottom|bottom-right", "confidence": "high|medium|low"}
  ]
}
Only report symbols that visually match one of the reference pictures above. Return ONLY the JSON object — no prose, no markdown fences."""


def _parse_vision_response(raw: str) -> Dict[str, Any]:
    """Parse Claude's {"findings": [...], "symbols": [...]} response, tolerating
    markdown code fences and any extra prose around the JSON object."""
    if not raw:
        return {'findings': [], 'symbols': []}
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\{.*\}', text, flags=re.DOTALL)
        if not m:
            return {'findings': [], 'symbols': []}
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return {'findings': [], 'symbols': []}
    if not isinstance(parsed, dict):
        return {'findings': [], 'symbols': []}
    return {
        'findings': parsed.get('findings') or [],
        'symbols': parsed.get('symbols') or [],
    }


def run_claude_analysis(
    drawing_data: dict,
    api_key: str,
    page_image_b64: str,
    symbol_images: Optional[List[Dict[str, Any]]] = None,
    model: Optional[str] = None,
    max_tokens: int = 4096,
    include_findings: bool = True,
) -> Dict[str, Any]:
    """
    Real Vision-based P&ID page analysis using Claude — sends the ACTUAL
    rendered page image (not just the OCR'd text) alongside the already-
    extracted tags/instruments/etc. as grounding context, and optionally a
    batch of labeled reference symbol pictures (LegendSymbolImage, via
    apps.pid_checker_v2) so the same call can also do visual symbol
    recognition — one Vision call covers both instead of two separate passes.

    Args:
        drawing_data: OCR-extracted tags/instruments/valves/etc for this page
                      (grounding context — ignored when include_findings=False)
        api_key: User-provided Claude API key (format: sk-ant-...)
        page_image_b64: base64 PNG of the ACTUAL rendered page — required
        symbol_images: [{'symbol_type': str, 'b64': str}, ...] reference
                       pictures for this call's batch (see legend_bridge.py)
        model: overrides apps.pid_checker_v2's VISION_MODELS['claude']
               (defaults to claude-sonnet-5)
        include_findings: False for symbol-only overflow-batch calls (when
                          symbol_images is large enough to need >1 call —
                          findings only need to be requested once per page)

    Returns:
        {'findings': [...], 'symbols': [...]}  (findings == [] when
        include_findings=False)

    Raises:
        ValueError: If API key or page_image_b64 is missing
        RuntimeError: If the Claude API call fails after retries
    """
    if not api_key or not api_key.startswith('sk-ant-'):
        raise ValueError("Invalid Claude API key format")
    if not page_image_b64:
        raise ValueError("page_image_b64 is required — Vision needs the actual rendered page image")

    from apps.pid_checker_v2.services.vision_extractor import (
        VISION_MODELS, VISION_MODEL_CLAUDE_FALLBACK, VISION_REQUEST_TIMEOUT_S,
        VISION_RETRY_MAX_ATTEMPTS, VISION_RETRY_BASE_DELAY_S, VISION_RETRY_MAX_DELAY_S,
        VISION_RETRY_STATUS_CODES, _extract_status_code, _is_overloaded_error,
        _is_model_not_found_error,
    )

    resolved_model = model or VISION_MODELS['claude']

    # ── Build the message content ───────────────────────────────────────
    # Reference symbol pictures come FIRST and are marked cacheable
    # (cache_control) — they're the same bytes on every page/batch call
    # within one document run, so putting them as a stable prefix lets
    # Anthropic's prompt cache skip re-billing them on every subsequent
    # call. The page-specific image/text (different every call) comes
    # after the cache breakpoint, so it's never part of the cached prefix.
    content: List[Dict[str, Any]] = []
    if symbol_images:
        content.append({
            'type': 'text',
            'text': (
                f'Reference symbol pictures ({len(symbol_images)} in this batch), '
                'each labeled with its exact name — use these to visually identify '
                'matching symbols on the P&ID page image below:'
            ),
        })
        for item in symbol_images:
            content.append({'type': 'text', 'text': f"Symbol: {item['symbol_type']}"})
            content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': item['b64']}})
        # Cache breakpoint — everything above this point (the reference
        # picture set) is a stable, reusable prefix across calls.
        content[-1] = {**content[-1], 'cache_control': {'type': 'ephemeral'}}

    if include_findings:
        content.append({'type': 'text', 'text': _build_claude_prompt(drawing_data)})
    content.append({'type': 'text', 'text': 'P&ID drawing page image (the actual rendered page):'})
    content.append({'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': page_image_b64}})
    content.append({
        'type': 'text',
        'text': _VISION_RESPONSE_INSTRUCTIONS_WITH_FINDINGS if include_findings
                else _VISION_RESPONSE_INSTRUCTIONS_SYMBOLS_ONLY,
    })

    def _call(use_model: str):
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, timeout=VISION_REQUEST_TIMEOUT_S)
        return client.messages.create(
            model=use_model,
            max_tokens=max_tokens,
            # No `temperature` — Claude Sonnet 5 / Opus 5 reject any
            # non-default temperature/top_p/top_k with a 400 on every
            # request (see the matching fix in vision_extractor.py's
            # _call_claude()).
            system=_VISION_SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': content}],
        )

    logger.info(
        "[Claude] Sending Vision request to %s (%d symbol image(s), include_findings=%s)",
        resolved_model, len(symbol_images or []), include_findings,
    )

    import time
    import anthropic

    use_model = resolved_model
    message = None
    last_exc: Optional[Exception] = None
    for attempt in range(1, VISION_RETRY_MAX_ATTEMPTS + 1):
        try:
            message = _call(use_model)
            break
        except anthropic.AuthenticationError:
            raise RuntimeError("Claude API key authentication failed. Please verify your key.")
        except ImportError:
            raise RuntimeError("Anthropic package not installed. Run: pip install anthropic")
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if use_model != VISION_MODEL_CLAUDE_FALLBACK and _is_model_not_found_error(exc):
                logger.warning(
                    "[Claude] model '%s' unavailable (%s) — retrying once with fallback '%s'",
                    use_model, exc, VISION_MODEL_CLAUDE_FALLBACK,
                )
                use_model = VISION_MODEL_CLAUDE_FALLBACK
                continue
            status = _extract_status_code(exc)
            retriable = status in VISION_RETRY_STATUS_CODES or _is_overloaded_error(exc)
            if not retriable or attempt == VISION_RETRY_MAX_ATTEMPTS:
                logger.error("[Claude] Vision analysis failed: %s", exc, exc_info=True)
                raise RuntimeError(f"Claude Vision analysis failed: {exc}")
            delay = min(VISION_RETRY_BASE_DELAY_S * (2 ** (attempt - 1)), VISION_RETRY_MAX_DELAY_S)
            logger.warning(
                "[Claude] transient error (status=%s attempt=%d/%d): %s — retrying in %.1fs",
                status, attempt, VISION_RETRY_MAX_ATTEMPTS, exc, delay,
            )
            time.sleep(delay)

    if message is None:
        raise RuntimeError(f"Claude Vision analysis failed: {last_exc}")

    content_text = ''.join(b.text for b in message.content if getattr(b, 'type', None) == 'text')
    parsed = _parse_vision_response(content_text)

    normalized_findings = []
    for idx, finding in enumerate(parsed.get('findings', []), start=1):
        normalized_findings.append({
            'category': finding.get('category', 'General'),
            'severity': str(finding.get('severity', 'medium')).lower(),
            'tag_number': finding.get('tag_number', 'N/A'),
            'issue_observed': finding.get('issue_observed', ''),
            'recommendation': finding.get('recommendation', ''),
            'rule_id': f"CLAUDE_{idx:03d}",
            'evidence': {
                'ai_model': use_model,
                'confidence': finding.get('confidence', 0.85),
                'source': 'Anthropic Claude Vision',
            },
        })

    symbols = []
    for s in parsed.get('symbols', []):
        symbol_type = str(s.get('symbol_type') or '').strip()
        if not symbol_type:
            continue
        confidence = str(s.get('confidence') or '').strip().lower()
        if confidence not in ('high', 'medium', 'low'):
            confidence = 'low'
        symbols.append({
            'symbol_type': symbol_type,
            'location': str(s.get('location') or 'unspecified').strip(),
            'confidence': confidence,
        })

    logger.info("[Claude] Vision analysis received %d finding(s), %d symbol(s)", len(normalized_findings), len(symbols))
    return {'findings': normalized_findings, 'symbols': symbols}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HYBRID ANALYSIS (OpenAI + Claude)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_hybrid_analysis(
    drawing_data: dict,
    openai_api_key: str,
    claude_api_key: str,
    page_image_b64: Optional[str] = None,
    symbol_images: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Hybrid analysis using both OpenAI (text-only) and Claude (real Vision,
    when page_image_b64 is provided) for cross-validation.

    Process:
    1. Run both analyses (Claude gets the actual page image + symbol images
       if provided; OpenAI stays text-only)
    2. Merge findings
    3. Cross-validate (flag common issues as higher confidence)
    4. Return unified findings + Claude's visually-identified symbols

    Args:
        drawing_data: Extracted drawing data
        openai_api_key: User-provided OpenAI API key
        claude_api_key: User-provided Claude API key
        page_image_b64: actual rendered page image for Claude's Vision call.
                         If omitted, the Claude leg is skipped (not failed)
                         since it can no longer run text-only.
        symbol_images: reference symbol pictures for Claude's Vision call

    Returns:
        {'findings': [...], 'symbols': [...]}  (symbols come from Claude only)

    Raises:
        RuntimeError: If both analyses fail (or the only available one does)
    """
    logger.info("[Hybrid] Running dual-AI analysis (OpenAI + Claude)")

    openai_findings: List[Dict[str, Any]] = []
    claude_findings: List[Dict[str, Any]] = []
    claude_symbols: List[Dict[str, Any]] = []
    errors = []

    # Run OpenAI analysis
    try:
        openai_result = run_openai_analysis(drawing_data, openai_api_key)
        openai_findings = openai_result['findings']
        logger.info("[Hybrid] OpenAI completed: %d findings", len(openai_findings))
    except Exception as e:
        error_msg = f"OpenAI failed: {str(e)}"
        logger.error("[Hybrid] %s", error_msg)
        errors.append(error_msg)

    # Run Claude analysis — only possible with a real page image now
    if page_image_b64:
        try:
            claude_result = run_claude_analysis(drawing_data, claude_api_key, page_image_b64, symbol_images=symbol_images)
            claude_findings = claude_result['findings']
            claude_symbols = claude_result['symbols']
            logger.info("[Hybrid] Claude completed: %d findings, %d symbols", len(claude_findings), len(claude_symbols))
        except Exception as e:
            error_msg = f"Claude failed: {str(e)}"
            logger.error("[Hybrid] %s", error_msg)
            errors.append(error_msg)
    else:
        logger.warning("[Hybrid] No page_image_b64 provided — skipping Claude Vision leg")

    # If both failed (or both unavailable), raise error
    if not openai_findings and not claude_findings:
        raise RuntimeError(f"Both AI analyses failed or unavailable. Errors: {'; '.join(errors) or 'no page image for Claude'}")

    # Merge and deduplicate findings
    merged = _merge_findings(openai_findings, claude_findings)

    logger.info("[Hybrid] Final merged results: %d findings, %d symbols", len(merged), len(claude_symbols))
    return {'findings': merged, 'symbols': claude_symbols}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HELPER FUNCTIONS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _build_openai_prompt(drawing_data: dict) -> str:
    """Build structured prompt for OpenAI"""
    instruments = drawing_data.get('instruments', [])
    valves = drawing_data.get('valves', [])
    lines = drawing_data.get('lines', [])
    
    prompt = f"""Analyze this P&ID drawing data for quality issues:

**Instruments**: {len(instruments)} total
{json.dumps(instruments[:20], indent=2) if instruments else "None"}

**Valves**: {len(valves)} total
{json.dumps(valves[:20], indent=2) if valves else "None"}

**Lines**: {len(lines)} total
{json.dumps(lines[:20], indent=2) if lines else "None"}

Identify issues and provide findings in JSON format."""
    
    return prompt


def _build_claude_prompt(drawing_data: dict) -> str:
    """Build structured prompt for Claude"""
    instruments = drawing_data.get('instruments', [])
    valves = drawing_data.get('valves', [])
    lines = drawing_data.get('lines', [])
    
    prompt = f"""# P&ID Quality Analysis Request

## Drawing Data Summary
- Instruments: {len(instruments)}
- Valves: {len(valves)}
- Lines: {len(lines)}

## Instrument Details
```json
{json.dumps(instruments[:20], indent=2) if instruments else "[]"}
```

## Valve Details
```json
{json.dumps(valves[:20], indent=2) if valves else "[]"}
```

## Line Details
```json
{json.dumps(lines[:20], indent=2) if lines else "[]"}
```

## Analysis Request
Perform comprehensive P&ID verification and identify all quality issues, inconsistencies, and improvement opportunities."""
    
    return prompt


def _merge_findings(
    openai_findings: List[Dict[str, Any]],
    claude_findings: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Merge and deduplicate findings from both AI sources.
    
    Logic:
    - Findings with same tag_number and similar issue text are considered duplicates
    - Duplicates are merged with higher confidence and 'HYBRID_' rule_id
    - Unique findings from each source are kept with original rule_id
    """
    merged = []
    used_claude_indices = set()
    
    # First pass: Find matching findings
    for openai_finding in openai_findings:
        tag = openai_finding.get('tag_number', '')
        issue = openai_finding.get('issue_observed', '').lower()
        
        # Look for similar finding in Claude results
        match_idx = None
        for idx, claude_finding in enumerate(claude_findings):
            if idx in used_claude_indices:
                continue
            
            claude_tag = claude_finding.get('tag_number', '')
            claude_issue = claude_finding.get('issue_observed', '').lower()
            
            # Check if similar (same tag and overlapping keywords)
            if tag == claude_tag and _has_keyword_overlap(issue, claude_issue):
                match_idx = idx
                break
        
        if match_idx is not None:
            # Merge findings
            claude_finding = claude_findings[match_idx]
            used_claude_indices.add(match_idx)
            
            merged_finding = {
                'category': openai_finding['category'],
                'severity': _max_severity(openai_finding['severity'], claude_finding['severity']),
                'tag_number': tag,
                'issue_observed': f"{openai_finding['issue_observed']} | Claude: {claude_finding['issue_observed']}",
                'recommendation': openai_finding['recommendation'],
                'rule_id': f"HYBRID_{len(merged) + 1:03d}",
                'evidence': {
                    'ai_model': 'OpenAI GPT-4o + Claude 3.5 Sonnet',
                    'confidence': 0.95,  # Higher confidence when both agree
                    'source': 'Hybrid Cross-Validation'
                }
            }
            merged.append(merged_finding)
        else:
            # Keep OpenAI finding as-is
            merged.append(openai_finding)
    
    # Second pass: Add unique Claude findings
    for idx, claude_finding in enumerate(claude_findings):
        if idx not in used_claude_indices:
            merged.append(claude_finding)
    
    return merged


def _has_keyword_overlap(text1: str, text2: str, threshold: float = 0.3) -> bool:
    """Check if two texts have significant keyword overlap"""
    words1 = set(text1.split())
    words2 = set(text2.split())
    
    # Remove common words
    common_words = {'a', 'the', 'is', 'are', 'and', 'or', 'in', 'on', 'at', 'to', 'for', 'of', 'with'}
    words1 = {w for w in words1 if w not in common_words and len(w) > 3}
    words2 = {w for w in words2 if w not in common_words and len(w) > 3}
    
    if not words1 or not words2:
        return False
    
    overlap = len(words1 & words2)
    total = min(len(words1), len(words2))
    
    return (overlap / total) >= threshold if total > 0 else False


def _max_severity(sev1: str, sev2: str) -> str:
    """Return the higher severity level"""
    severity_rank = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}
    rank1 = severity_rank.get(sev1.lower(), 1)
    rank2 = severity_rank.get(sev2.lower(), 1)
    
    for sev, rank in severity_rank.items():
        if rank == max(rank1, rank2):
            return sev
    return 'medium'


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CONVERSION — AI findings (dicts) → RuleFinding (pipeline/persistence format)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Soft-coded: ai_analysis severities (critical/high/medium/low) do not match
# PIDVFinding.Severity choices (critical/major/minor/info) — map explicitly.
SEVERITY_MAP_TO_PIDV = {
    'critical': 'critical',
    'high':     'major',
    'medium':   'minor',
    'low':      'info',
}

# Soft-coded: category persisted for every AI-generated finding. PIDVFinding.category
# is not enforced at the DB level (only via full_clean()), so a value outside the
# Category.choices enum is safe — mirrors the 'legend'/'linelist'/etc. comparison
# categories which are also outside that enum.
AI_FINDING_CATEGORY = 'ai_insight'


def to_rule_findings(ai_findings: List[Dict[str, Any]]) -> List[Any]:
    """
    Convert AI analysis findings (plain dicts, as returned by run_openai_analysis /
    run_claude_analysis / run_hybrid_analysis) into `RuleFinding` objects so they can
    be merged with rule-engine/comparison-engine findings and persisted as
    PIDVFinding rows via the same code path.
    """
    from apps.pid_verification_v2.services.rule_engine import RuleFinding

    converted = []
    for finding in ai_findings:
        raw_severity = str(finding.get('severity', 'medium')).lower()
        severity = SEVERITY_MAP_TO_PIDV.get(raw_severity, 'minor')

        tag_number = finding.get('tag_number', 'N/A')
        evidence_meta = finding.get('evidence') or {}
        evidence = (
            f"Tag: {tag_number} | Model: {evidence_meta.get('ai_model', 'AI')} | "
            f"Confidence: {evidence_meta.get('confidence', 'N/A')} | "
            f"Source: {evidence_meta.get('source', 'AI Analysis')}"
        )

        converted.append(RuleFinding(
            category=AI_FINDING_CATEGORY,
            rule_id=finding.get('rule_id', 'AI-000'),
            issue_observed=finding.get('issue_observed', ''),
            action_required=finding.get('recommendation', 'Review AI-flagged issue'),
            evidence=evidence,
            direction='N/A',
            severity=severity,
        ))
    return converted


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SMART VALUE COMPARISON — Claude judges "same real-world value?" for pairs
# comparison_engine.py's naive fuzzy_match() can't resolve confidently
# (unit differences, format differences, ranges, abbreviations). Purely
# text reasoning — no image needed, unlike run_claude_analysis() above.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

SMART_COMPARE_VALID_RESULTS = ('MATCH', 'MISMATCH', 'UNCERTAIN')
SMART_COMPARE_VALID_CONFIDENCE = ('HIGH', 'MEDIUM', 'LOW')

# Soft-coded: cap how many pairs go in one Claude call. A P&ID with hundreds
# of ambiguous attribute pairs would otherwise produce one enormous prompt —
# batching keeps each call's token count and failure blast-radius bounded,
# while still being a large improvement over one call per pair.
SMART_COMPARE_BATCH_SIZE = 40

_SMART_COMPARE_SYSTEM_PROMPT = (
    "You are a senior process engineer auditing a P&ID against reference "
    "documents (Line List, Equipment Register, Instrument Index). Apply "
    "engineering judgment and common sense to EVERY pair below, the same "
    "way an experienced engineer reviewing these documents side-by-side "
    "would — don't do a rigid literal string comparison. "
    "In particular, always account for: "
    "(1) Unit differences, abbreviations, and unicode/superscript forms — "
    "these never change whether two values are the same real-world "
    "quantity (e.g. 15.0 M = 15000 mm; 150 psig = 150 when the unit is "
    "implied elsewhere; 327 m³ = 327 M3 = 327 CUM = 327 cubic meters). "
    "(2) Case, spacing, punctuation, and formatting differences never "
    "matter on their own (e.g. 'CS + LINING' = 'CS + Lining'). "
    "(3) Min/Max ranges: when one side is a combined range ('Min: 60 / "
    "Max: 105') and the other is a single value, that value matches if it "
    "falls anywhere inside the range OR equals one of its endpoints. When "
    "BOTH sides express a range (e.g. an 'OT Min'/'OT Max' pair of fields "
    "vs a combined 'Min:60/Max:105' cell), compare Min-to-Min and "
    "Max-to-Max independently — each endpoint must correspond to the "
    "matching endpoint on the other side, not just fall somewhere in the "
    "overall range. "
    "(4) Numeric rounding/precision differences within about 1% represent "
    "the same value (e.g. 100.0 and 99.6 are the same). "
    "(5) A missing/blank value on one side is not automatically a "
    "mismatch — a genuinely blank reference cell for an optional attribute "
    "is UNCERTAIN, not MISMATCH, unless the P&ID explicitly contradicts it. "
    "This engineering-judgment approach applies uniformly across ALL "
    "comparison types you're asked about — line list, equipment register, "
    "instrument index, and legend/symbol cross-checks alike — not just the "
    "examples listed here."
)

_SMART_COMPARE_INSTRUCTIONS = """For each pair, respond with exactly one of:
- MATCH: you are confident they represent the same real-world value
- MISMATCH: you are confident they represent different real-world values
- UNCERTAIN: you cannot confidently tell either way — a human engineer should verify

Also give a confidence level (HIGH/MEDIUM/LOW) and a ONE-SENTENCE explanation
of your reasoning (e.g. "same numeric value, different units (psig vs bare
number)" or "P&ID value present, reference value missing").

Return ONLY a JSON array, one object per pair, in the SAME ORDER as the
pairs below — no prose, no markdown fences:
[{"index": 0, "result": "MATCH", "confidence": "HIGH", "explanation": "..."}]

Pairs to compare:
"""


def smart_compare_batch(pairs: List[Dict[str, Any]], api_key: str,
                         model: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Ask Claude whether each (P&ID value, reference value) pair represents
    the same real-world thing — handles unit conversions, formatting
    differences, and ranges that comparison_engine.py's plain string
    fuzzy-match can't resolve confidently.

    Args:
        pairs: [{'label': str, 'pid_value': str, 'ref_value': str}, ...]
               `label` is just context for the prompt (e.g. "Equipment type
               for V-803-TF") — not returned, matched back by list position.
        api_key: BYOK Claude API key (required — raises ValueError if missing)
        model: overrides the default (claude-sonnet-5)

    Returns:
        One dict per input pair, SAME ORDER, even on partial failure —
        entries Claude's response is missing/malformed for default to
        {'result': 'UNCERTAIN', 'confidence': 'LOW', 'explanation': '...'}
        rather than being silently dropped, so callers can always zip()
        this 1:1 against `pairs`.

    Never raises for the AI call itself failing (network/parse errors) —
    returns an all-UNCERTAIN list instead, so a transient API hiccup
    degrades to "ask the engineer" rather than crashing the whole
    comparison. Only a missing api_key raises (caller's responsibility to
    not call this without one).
    """
    if not api_key or not api_key.startswith('sk-ant-'):
        raise ValueError("Invalid Claude API key format")
    if not pairs:
        return []

    from apps.pid_checker_v2.services.vision_extractor import (
        VISION_MODELS, VISION_MODEL_CLAUDE_FALLBACK, VISION_REQUEST_TIMEOUT_S,
        VISION_RETRY_MAX_ATTEMPTS, VISION_RETRY_BASE_DELAY_S, VISION_RETRY_MAX_DELAY_S,
        VISION_RETRY_STATUS_CODES, _extract_status_code, _is_overloaded_error,
        _is_model_not_found_error,
    )

    resolved_model = model or VISION_MODELS['claude']
    all_results: List[Dict[str, Any]] = [None] * len(pairs)  # filled in per-batch below

    batches = [pairs[i:i + SMART_COMPARE_BATCH_SIZE] for i in range(0, len(pairs), SMART_COMPARE_BATCH_SIZE)]
    offset = 0
    for batch in batches:
        batch_results = _smart_compare_one_batch(
            batch, api_key, resolved_model, VISION_MODEL_CLAUDE_FALLBACK, VISION_REQUEST_TIMEOUT_S,
            VISION_RETRY_MAX_ATTEMPTS, VISION_RETRY_BASE_DELAY_S, VISION_RETRY_MAX_DELAY_S,
            VISION_RETRY_STATUS_CODES, _extract_status_code, _is_overloaded_error, _is_model_not_found_error,
        )
        for i, result in enumerate(batch_results):
            all_results[offset + i] = result
        offset += len(batch)

    return all_results


def _smart_compare_one_batch(batch, api_key, resolved_model, fallback_model, timeout_s,
                              retry_max_attempts, retry_base_delay, retry_max_delay,
                              retry_status_codes, extract_status_code, is_overloaded_error,
                              is_model_not_found_error) -> List[Dict[str, Any]]:
    """One Claude call covering up to SMART_COMPARE_BATCH_SIZE pairs. Never
    raises — returns an all-UNCERTAIN list of the same length on any failure."""
    fallback = [
        {'result': 'UNCERTAIN', 'confidence': 'LOW', 'explanation': 'AI comparison unavailable — please verify manually.'}
        for _ in batch
    ]

    lines = []
    for i, pair in enumerate(batch):
        label = pair.get('label') or f'item {i}'
        pid_val = pair.get('pid_value')
        ref_val = pair.get('ref_value')
        pid_str = pid_val if pid_val not in (None, '') else '(missing)'
        ref_str = ref_val if ref_val not in (None, '') else '(missing)'
        lines.append(f'[{i}] {label} | P&ID: {pid_str!r} | Reference: {ref_str!r}')
    prompt = _SMART_COMPARE_INSTRUCTIONS + '\n'.join(lines)

    def _call(use_model: str):
        import anthropic
        client = anthropic.Anthropic(api_key=api_key, timeout=timeout_s)
        resp = client.messages.create(
            model=use_model,
            max_tokens=4096,
            # No `temperature` — Claude Sonnet 5 / Opus 5 reject any
            # non-default temperature/top_p/top_k with a 400 on every
            # request (see the matching fix in vision_extractor.py).
            system=_SMART_COMPARE_SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return ''.join(b.text for b in resp.content if getattr(b, 'type', None) == 'text')

    import time
    use_model = resolved_model
    raw = None
    for attempt in range(1, retry_max_attempts + 1):
        try:
            raw = _call(use_model)
            break
        except Exception as exc:  # noqa: BLE001
            if use_model != fallback_model and is_model_not_found_error(exc):
                logger.warning("[SmartCompare] model '%s' unavailable — retrying with fallback '%s'", use_model, fallback_model)
                use_model = fallback_model
                continue
            status = extract_status_code(exc)
            retriable = status in retry_status_codes or is_overloaded_error(exc)
            if not retriable or attempt == retry_max_attempts:
                logger.error("[SmartCompare] Batch comparison failed: %s", exc, exc_info=True)
                return fallback
            delay = min(retry_base_delay * (2 ** (attempt - 1)), retry_max_delay)
            logger.warning("[SmartCompare] transient error (attempt %d/%d): %s — retrying in %.1fs",
                           attempt, retry_max_attempts, exc, delay)
            time.sleep(delay)

    if raw is None:
        return fallback

    parsed = _parse_smart_compare_response(raw, len(batch))
    return parsed if parsed is not None else fallback


def _parse_smart_compare_response(raw: str, expected_count: int) -> Optional[List[Dict[str, Any]]]:
    """Parse Claude's JSON array response, tolerant of markdown fences and
    out-of-order/missing indices. Returns None (caller falls back to
    all-UNCERTAIN) only if nothing usable could be parsed at all."""
    if not raw:
        return None
    text = raw.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r'\[.*\]', text, flags=re.DOTALL)
        if not m:
            return None
        try:
            parsed = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(parsed, list):
        return None

    by_index: Dict[int, Dict[str, Any]] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get('index'))
        except (TypeError, ValueError):
            continue
        result = str(item.get('result') or '').strip().upper()
        if result not in SMART_COMPARE_VALID_RESULTS:
            result = 'UNCERTAIN'
        confidence = str(item.get('confidence') or '').strip().upper()
        if confidence not in SMART_COMPARE_VALID_CONFIDENCE:
            confidence = 'LOW'
        by_index[idx] = {
            'result': result,
            'confidence': confidence,
            'explanation': str(item.get('explanation') or '').strip(),
        }

    if not by_index:
        return None

    # Fill any gaps (Claude skipped/miscounted an index) with UNCERTAIN
    # rather than dropping that pair from the output entirely.
    return [
        by_index.get(i) or {'result': 'UNCERTAIN', 'confidence': 'LOW', 'explanation': 'No AI response for this pair.'}
        for i in range(expected_count)
    ]
