"""
Document Intelligence Engine (MODULE 2) — rule-based extraction, optionally
augmented by a per-project BYOK Claude call (see services/claude_client.py).

IMPORTANT: The deterministic keyword/regex analyzer below always runs first
and is the guaranteed result — it is never overridden or blocked by the
optional Claude augmentation. Swap in a real AI call later behind the same
`analyze_project()` function signature without touching callers.
"""
from __future__ import annotations

import json
import re

from ..config import CLAUDE_MAX_INPUT_CHARS, CLAUDE_INTELLIGENCE_MAX_TOKENS, DISCIPLINE_DEFAULT_DELIVERABLES, DEFAULT_HSE_STUDIES
from . import claude_client

_EFFECTIVE_DATE_RE = re.compile(
    r'(effective date|zero date|contract award)[^\n]{0,40}?'
    r'(\d{1,2}[\-/][A-Za-z]{3,9}[\-/]\d{2,4}|\d{4}-\d{2}-\d{2})',
    re.IGNORECASE,
)
_PROJECT_NAME_RE = re.compile(r'(?:project title|project name)\s*[:\-]\s*(.+)', re.IGNORECASE)
_DURATION_RE = re.compile(r'(\d{1,2})\s*[- ]?month', re.IGNORECASE)

_CLAUDE_SYSTEM_PROMPT = (
    'You are an engineering project-controls assistant reviewing source documents '
    '(SOW, WBS, MDR, EDDR, schedule requirements) for a FEED/DEFINE oil & gas project. '
    'A deterministic keyword analyzer has already extracted a baseline. Your job is to '
    'review the raw text and the baseline, then respond with STRICT JSON only (no markdown '
    'fences, no prose outside the JSON object) matching this shape: '
    '{"project_name": string|null, "effective_date": string|null, "duration_months": integer|null, '
    '"additional_notes": string, "review_summary": string}. '
    '"additional_notes" should call out anything the baseline appears to have missed '
    '(e.g. deliverables, disciplines, risks) as short advisory text — it is informational '
    'only and will NOT replace the baseline deliverable catalogue. '
    '"review_summary" is a 2-3 sentence plain-English summary of the source documents. '
    'If you are unsure of a field, use null. Do not invent facts not present in the text.'
)


def _detect_disciplines_and_deliverables(all_text: str) -> dict:
    """For each discipline, flag whether any of its default deliverables are
    mentioned in the source text; always keep the full default list as the
    generation fallback (per MVP: never block generation on imperfect NLP)."""
    lower = all_text.lower()
    result = {}
    for discipline, deliverables in DISCIPLINE_DEFAULT_DELIVERABLES.items():
        detected = [d for d in deliverables if d.lower() in lower]
        result[discipline] = {
            'mentioned_in_source': detected,
            'deliverables': deliverables,  # always use the full soft-coded catalogue
        }
    return result


def _detect_hse_studies(all_text: str) -> list:
    lower = all_text.lower()
    detected = [s for s in DEFAULT_HSE_STUDIES if s.lower() in lower]
    return detected or DEFAULT_HSE_STUDIES


def _augment_with_claude(intelligence: dict, combined_text: str, project, user) -> None:
    """Mutate `intelligence` in place with an optional Claude review. Never
    raises and never removes/overrides deterministic fields — only adds an
    `ai_review` sub-dict plus `ai_augmented` / `ai_provider_used` flags."""
    intelligence['ai_augmented'] = False
    intelligence['ai_provider_used'] = None

    if claude_client.get_claude_config(project) is None:
        return

    baseline_summary = {
        'detected_project_name': intelligence.get('detected_project_name'),
        'detected_effective_date_text': intelligence.get('detected_effective_date_text'),
        'detected_duration_months': intelligence.get('detected_duration_months'),
        'categories_present': intelligence.get('categories_present'),
    }
    user_prompt = (
        f'BASELINE (deterministic analyzer output):\n{json.dumps(baseline_summary)}\n\n'
        f'SOURCE TEXT (truncated):\n{combined_text[:CLAUDE_MAX_INPUT_CHARS]}'
    )

    result = claude_client.call_claude(
        project,
        system_prompt=_CLAUDE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=CLAUDE_INTELLIGENCE_MAX_TOKENS,
        feature='document_intelligence',
        user=user,
    )
    if result is None:
        intelligence['notes'].append(
            'Claude BYOK is enabled for this project but the augmentation call did not '
            'succeed this run — showing deterministic analysis only.'
        )
        return

    try:
        parsed = json.loads(result['text'])
    except (ValueError, TypeError):
        intelligence['notes'].append(
            'Claude BYOK responded but the review could not be parsed — showing '
            'deterministic analysis only.'
        )
        return

    intelligence['ai_review'] = {
        'project_name': parsed.get('project_name'),
        'effective_date': parsed.get('effective_date'),
        'duration_months': parsed.get('duration_months'),
        'additional_notes': parsed.get('additional_notes') or '',
        'review_summary': parsed.get('review_summary') or '',
    }
    intelligence['ai_augmented'] = True
    intelligence['ai_provider_used'] = 'anthropic'


def analyze_project(files_qs, project=None, user=None) -> dict:
    """
    files_qs: iterable of PlanningFile instances (already parsed).
    project: optional PlanningProject — when it has BYOK/Claude configured,
        the deterministic result below is augmented (never replaced).
    user: optional request user, for AI usage-log attribution only.
    Returns a JSON-serialisable "extracted intelligence" dict.
    """
    files_list = list(files_qs)
    combined_text = '\n'.join(f.extracted_text or '' for f in files_list)

    project_name_match = _PROJECT_NAME_RE.search(combined_text)
    effective_date_match = _EFFECTIVE_DATE_RE.search(combined_text)
    duration_match = _DURATION_RE.search(combined_text)

    categories_present = sorted({f.category for f in files_list})

    intelligence = {
        'source_file_count': len(files_list),
        'categories_present': categories_present,
        'detected_project_name': project_name_match.group(1).strip()[:255] if project_name_match else None,
        'detected_effective_date_text': effective_date_match.group(2) if effective_date_match else None,
        'detected_duration_months': int(duration_match.group(1)) if duration_match else None,
        'disciplines': _detect_disciplines_and_deliverables(combined_text),
        'hse_studies': _detect_hse_studies(combined_text),
        'notes': [
            'Document intelligence is generated by a deterministic keyword/pattern '
            'analyzer (no external AI API is configured). Review before finalizing.',
        ],
    }

    _augment_with_claude(intelligence, combined_text, project, user)
    return intelligence

