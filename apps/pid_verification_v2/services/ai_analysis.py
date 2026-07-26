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
) -> List[Dict[str, Any]]:
    """
    Enhanced P&ID analysis using OpenAI GPT-4o.
    
    Args:
        drawing_data: Extracted drawing data (instruments, valves, lines, etc.)
        api_key: User-provided OpenAI API key (format: sk-...)
        model: OpenAI model name (default: gpt-4o)
        temperature: Sampling temperature (0-1, lower = more deterministic)
    
    Returns:
        List of findings dictionaries with keys:
            - category: str
            - severity: 'critical' | 'high' | 'medium' | 'low'
            - tag_number: str
            - issue_observed: str
            - recommendation: str
            - rule_id: str (prefixed with 'OPENAI_')
    
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
        return normalized
        
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

def run_claude_analysis(
    drawing_data: dict,
    api_key: str,
    model: str = "claude-3-5-sonnet-20241022",
    max_tokens: int = 4000,
) -> List[Dict[str, Any]]:
    """
    Deep P&ID analysis using Anthropic Claude 3.5 Sonnet.
    
    Args:
        drawing_data: Extracted drawing data
        api_key: User-provided Claude API key (format: sk-ant-...)
        model: Claude model name
        max_tokens: Maximum response tokens
    
    Returns:
        List of findings dictionaries (same format as OpenAI)
    
    Raises:
        ValueError: If API key is invalid
        RuntimeError: If Claude API call fails
    """
    if not api_key or not api_key.startswith('sk-ant-'):
        raise ValueError("Invalid Claude API key format")
    
    try:
        import anthropic
        
        # Initialize client with user's key
        client = anthropic.Anthropic(api_key=api_key)
        
        # Build structured prompt
        prompt = _build_claude_prompt(drawing_data)
        
        logger.info("[Claude] Sending request to %s (max_tokens=%d)", model, max_tokens)
        
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0.2,
            system="""You are a world-class P&ID verification specialist with deep expertise in process engineering, instrumentation, and safety systems.

Perform comprehensive analysis focusing on:
1. ISA standards compliance (S5.1, S18.1, S84)
2. Safety Instrumented Systems (SIS) design
3. Process hazard analysis
4. Material selection and compatibility
5. Flow assurance and hydraulics
6. Control system architecture

Provide detailed, actionable findings with severity assessment.""",
            messages=[
                {
                    "role": "user",
                    "content": prompt + "\n\nReturn findings as JSON array with keys: category, severity, tag_number, issue_observed, recommendation, confidence."
                }
            ]
        )
        
        # Parse response
        content = message.content[0].text
        
        # Try to extract JSON from response
        json_start = content.find('[')
        json_end = content.rfind(']') + 1
        
        if json_start >= 0 and json_end > json_start:
            json_str = content[json_start:json_end]
            findings = json.loads(json_str)
        else:
            # If no JSON array found, try to parse as JSON object
            findings_data = json.loads(content)
            findings = findings_data.get('findings', [])
        
        # Normalize findings
        normalized = []
        
        for idx, finding in enumerate(findings, start=1):
            normalized.append({
                'category': finding.get('category', 'General'),
                'severity': finding.get('severity', 'medium').lower(),
                'tag_number': finding.get('tag_number', 'N/A'),
                'issue_observed': finding.get('issue_observed', ''),
                'recommendation': finding.get('recommendation', ''),
                'rule_id': f"CLAUDE_{idx:03d}",
                'evidence': {
                    'ai_model': model,
                    'confidence': finding.get('confidence', 0.85),
                    'source': 'Anthropic Claude 3.5 Sonnet'
                }
            })
        
        logger.info("[Claude] Received %d findings", len(normalized))
        return normalized
        
    except ImportError:
        raise RuntimeError("Anthropic package not installed. Run: pip install anthropic")
    except anthropic.AuthenticationError:
        raise RuntimeError("Claude API key authentication failed. Please verify your key.")
    except anthropic.RateLimitError:
        raise RuntimeError("Claude API rate limit exceeded. Please try again later.")
    except Exception as e:
        logger.error("[Claude] Analysis failed: %s", str(e), exc_info=True)
        raise RuntimeError(f"Claude analysis failed: {str(e)}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# HYBRID ANALYSIS (OpenAI + Claude)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_hybrid_analysis(
    drawing_data: dict,
    openai_api_key: str,
    claude_api_key: str,
) -> List[Dict[str, Any]]:
    """
    Hybrid analysis using both OpenAI and Claude for cross-validation.
    
    Process:
    1. Run both analyses in parallel
    2. Merge findings
    3. Cross-validate (flag common issues as higher confidence)
    4. Return unified findings list
    
    Args:
        drawing_data: Extracted drawing data
        openai_api_key: User-provided OpenAI API key
        claude_api_key: User-provided Claude API key
    
    Returns:
        Combined and deduplicated findings list
    
    Raises:
        RuntimeError: If both analyses fail
    """
    logger.info("[Hybrid] Running dual-AI analysis (OpenAI + Claude)")
    
    openai_findings = []
    claude_findings = []
    errors = []
    
    # Run OpenAI analysis
    try:
        openai_findings = run_openai_analysis(drawing_data, openai_api_key)
        logger.info("[Hybrid] OpenAI completed: %d findings", len(openai_findings))
    except Exception as e:
        error_msg = f"OpenAI failed: {str(e)}"
        logger.error("[Hybrid] %s", error_msg)
        errors.append(error_msg)
    
    # Run Claude analysis
    try:
        claude_findings = run_claude_analysis(drawing_data, claude_api_key)
        logger.info("[Hybrid] Claude completed: %d findings", len(claude_findings))
    except Exception as e:
        error_msg = f"Claude failed: {str(e)}"
        logger.error("[Hybrid] %s", error_msg)
        errors.append(error_msg)
    
    # If both failed, raise error
    if not openai_findings and not claude_findings:
        raise RuntimeError(f"Both AI analyses failed. Errors: {'; '.join(errors)}")
    
    # Merge and deduplicate findings
    merged = _merge_findings(openai_findings, claude_findings)
    
    logger.info("[Hybrid] Final merged results: %d findings", len(merged))
    return merged


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
