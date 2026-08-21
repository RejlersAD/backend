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

from ..config import (
    CLAUDE_MAX_INPUT_CHARS, CLAUDE_INTELLIGENCE_MAX_TOKENS, CLAUDE_SCOPE_MAX_TOKENS,
    DISCIPLINE_DEFAULT_DELIVERABLES, DEFAULT_HSE_STUDIES, DISCIPLINE_NAME_BY_CODE,
    DELIVERABLE_ALIASES,
)
from . import claude_client

# Categories that materially describe project scope; when the planner has
# uploaded *only* SOW (no MDR/EDDR/WBS), we flag `sow_only_mode = True` so the
# UI can tell the user that BYOK will be doing the heavy lifting on scope.
_SCOPE_DEFINING_CATEGORIES = {'mdr', 'eddr', 'wbs'}

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
    mentioned in the source text (via canonical name OR any soft-coded alias);
    always keep the full default list as the generation fallback (per MVP:
    never block generation on imperfect NLP)."""
    lower = all_text.lower()
    result = {}
    for discipline, deliverables in DISCIPLINE_DEFAULT_DELIVERABLES.items():
        detected = []
        for canonical in deliverables:
            terms = [canonical.lower(), *[a.lower() for a in DELIVERABLE_ALIASES.get(canonical, [])]]
            if any(term in lower for term in terms):
                detected.append(canonical)
        result[discipline] = {
            'mentioned_in_source': detected,
            'deliverables': list(deliverables),
            'in_scope': True,
            # AI-discovered deliverables get merged in by the BYOK scope pass
            # below; the frontend uses this to badge them separately.
            'ai_discovered': [],
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

    # Fill in baseline fields the deterministic regex pass missed — Claude
    # reads the same source text and can often find these even when the
    # exact "Project Title:" / "Effective Date:" phrasing isn't present.
    # Never overrides a value the regex pass already found.
    if not intelligence.get('detected_project_name') and parsed.get('project_name'):
        intelligence['detected_project_name'] = str(parsed['project_name']).strip()[:255]
    if not intelligence.get('detected_effective_date_text') and parsed.get('effective_date'):
        intelligence['detected_effective_date_text'] = str(parsed['effective_date']).strip()
    if not intelligence.get('detected_duration_months') and parsed.get('duration_months'):
        try:
            intelligence['detected_duration_months'] = int(parsed['duration_months'])
        except (TypeError, ValueError):
            pass


_CLAUDE_SCOPE_SYSTEM_PROMPT = (
    'You are an oil & gas FEED/DEFINE project-controls scope analyst. You will '
    'receive a SOW / MDR / EDDR extract plus a fixed catalogue of engineering '
    'disciplines, their canonical deliverables, and HSE studies the platform is '
    'capable of scheduling. Your job is to decide which of those catalogue '
    'entries are ACTUALLY in scope for this specific project based on the source '
    'text, and to surface any deliverables the SOW requires that are NOT in the '
    'catalogue. Respond with STRICT JSON only (no markdown fences, no prose '
    'outside the JSON) matching: '
    '{"disciplines_in_scope": [<discipline code>, ...], '
    '"disciplines_out_of_scope": [<discipline code>, ...], '
    '"hse_studies_in_scope": [<hse study name>, ...], '
    '"deliverable_hints": {<discipline code>: [<new deliverable name>, ...]}, '
    '"authoritative_deliverables_by_discipline": {<discipline code>: [<deliverable>, ...]}, '
    '"scope_summary": string}. '
    'Rules: (a) use ONLY the discipline codes from the CATALOGUE — do not '
    'invent new ones; (b) if the source text is silent about a discipline / HSE '
    'study, LEAVE IT IN scope (safer to over-schedule than to drop scope '
    'silently); (c) only mark out_of_scope when the SOW clearly excludes it or '
    'the project type obviously does not need it; '
    '(d) "deliverable_hints" is the list of deliverables the SOW explicitly '
    'names that the catalogue is missing — return only genuinely new ones, do '
    'not repeat catalogue entries; '
    '(e) "authoritative_deliverables_by_discipline" — for every discipline you '
    'marked in-scope where the source text (SOW, MDR, EDDR, WBS — whichever '
    'was provided) names specific deliverables, return that real, explicit '
    'list (catalogue matches + new ones), in execution order. If the source '
    'is genuinely silent on a discipline\'s deliverables, leave that '
    'discipline out of this object entirely so the platform catalogue is '
    'used as the fallback for it — do not guess or invent a list. '
    '(f) "scope_summary" is 1-2 sentences on why this scope was chosen.'
)


def _augment_with_claude_scope(intelligence: dict, combined_text: str, project, user) -> None:
    """Second BYOK pass that asks Claude to decide which disciplines / HSE
    studies are actually in scope. Mutates `intelligence` in place:
    - flips `disciplines[<code>].in_scope` to False for disciplines Claude
      marked out of scope;
    - shrinks `hse_studies` to Claude's `hse_studies_in_scope` list (only
      when Claude returned a non-empty subset);
    - stores the raw payload under `intelligence['ai_scope']` for the UI to
      render as a badge / summary.
    Never raises, never removes the deterministic disciplines dict; the
    planner can always re-enable a discipline from the Edit panel."""
    intelligence['ai_scope'] = None

    if claude_client.get_claude_config(project) is None:
        return

    discipline_catalogue = {code: DISCIPLINE_NAME_BY_CODE.get(code, code) for code in DISCIPLINE_DEFAULT_DELIVERABLES.keys()}
    catalogue_payload = {
        'disciplines': discipline_catalogue,
        'deliverables_by_discipline': dict(DISCIPLINE_DEFAULT_DELIVERABLES),
        'hse_studies': list(DEFAULT_HSE_STUDIES),
    }
    sow_only = bool(intelligence.get('sow_only_mode'))
    user_prompt = (
        f'CATALOGUE (allowed values):\n{json.dumps(catalogue_payload)}\n\n'
        f'SOW-only mode: {sow_only}\n\n'
        f'SOURCE TEXT (truncated):\n{combined_text[:CLAUDE_MAX_INPUT_CHARS]}'
    )

    result = claude_client.call_claude(
        project,
        system_prompt=_CLAUDE_SCOPE_SYSTEM_PROMPT,
        user_prompt=user_prompt,
        max_tokens=CLAUDE_SCOPE_MAX_TOKENS,
        feature='document_intelligence_scope',
        user=user,
    )
    if result is None:
        intelligence['notes'].append(
            'Claude BYOK scope pass did not succeed this run — every discipline '
            'stays in scope by default.'
        )
        return

    try:
        parsed = json.loads(result['text'])
    except (ValueError, TypeError):
        intelligence['notes'].append(
            'Claude BYOK scope pass responded but the JSON could not be parsed — '
            'every discipline stays in scope by default.'
        )
        return

    in_scope_codes = {str(c).strip() for c in (parsed.get('disciplines_in_scope') or []) if str(c).strip()}
    out_of_scope_codes = {str(c).strip() for c in (parsed.get('disciplines_out_of_scope') or []) if str(c).strip()}
    hse_in_scope = [str(h).strip() for h in (parsed.get('hse_studies_in_scope') or []) if str(h).strip()]
    deliverable_hints_raw = parsed.get('deliverable_hints') or {}
    deliverable_hints = {
        str(k): [str(v).strip() for v in vs if str(v).strip()]
        for k, vs in deliverable_hints_raw.items()
        if isinstance(vs, list)
    }
    authoritative_raw = parsed.get('authoritative_deliverables_by_discipline') or {}
    authoritative = {
        str(k): [str(v).strip() for v in vs if str(v).strip()]
        for k, vs in authoritative_raw.items()
        if isinstance(vs, list)
    }
    scope_summary = parsed.get('scope_summary') or ''

    disciplines = intelligence.get('disciplines') or {}
    for code, info in disciplines.items():
        if code in out_of_scope_codes and code not in in_scope_codes:
            info['in_scope'] = False
        else:
            info['in_scope'] = True

        # When Claude (BYOK) returns an authoritative deliverable list for this
        # in-scope discipline — from ANY upload mode, not just SOW-only —
        # REPLACE the catalogue fallback with it: the source document is the
        # ground truth, the catalogue is only ever a safety net for when
        # Claude found nothing explicit (see _CLAUDE_SCOPE_SYSTEM_PROMPT rule e).
        # Deterministic (no-BYOK) mode is unaffected — `authoritative` is only
        # ever populated from a real Claude response.
        if code in authoritative and info.get('in_scope') is not False:
            info['deliverables'] = list(authoritative[code])
            info['mentioned_in_source'] = [d for d in authoritative[code]
                                            if d in DISCIPLINE_DEFAULT_DELIVERABLES.get(code, [])]

        # Merge Claude's discovered deliverables into every in-scope
        # discipline (both SOW-only and full-upload modes) — dedupe against
        # what's already there and track the AI-provenance for the UI badge.
        hints = deliverable_hints.get(code) or []
        if hints and info.get('in_scope') is not False:
            existing = set(info.get('deliverables') or [])
            existing_lower = {d.lower() for d in existing}
            discovered = info.setdefault('ai_discovered', [])
            for hint in hints:
                if hint.lower() in existing_lower:
                    continue
                info.setdefault('deliverables', []).append(hint)
                if hint not in info.get('mentioned_in_source', []):
                    info.setdefault('mentioned_in_source', []).append(hint)
                if hint not in discovered:
                    discovered.append(hint)
                existing_lower.add(hint.lower())

    # Only prune HSE studies when Claude returned a non-empty subset — an
    # empty list may just mean Claude was not confident, and the planner has
    # already been given the interactive HSE picker (see PlanningPackagePage).
    valid_hse = [h for h in hse_in_scope if h in DEFAULT_HSE_STUDIES]
    if valid_hse:
        intelligence['hse_studies'] = valid_hse

    intelligence['ai_scope'] = {
        'disciplines_in_scope': sorted(in_scope_codes),
        'disciplines_out_of_scope': sorted(out_of_scope_codes),
        'hse_studies_in_scope': valid_hse,
        'deliverable_hints': deliverable_hints,
        # Applies in any upload mode now (see rule e / the per-discipline loop
        # above) — this must mirror what was actually applied, not re-gate it.
        'authoritative_deliverables_by_discipline': authoritative,
        'sow_only_authoritative_applied': bool(authoritative),
        'scope_summary': scope_summary,
    }


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
    categories_set = set(categories_present)
    sow_only_mode = 'sow' in categories_set and not (categories_set & _SCOPE_DEFINING_CATEGORIES)

    intelligence = {
        'source_file_count': len(files_list),
        'categories_present': categories_present,
        'sow_only_mode': sow_only_mode,
        'detected_project_name': project_name_match.group(1).strip()[:255] if project_name_match else None,
        'detected_effective_date_text': effective_date_match.group(2) if effective_date_match else None,
        'detected_duration_months': int(duration_match.group(1)) if duration_match else None,
        'disciplines': _detect_disciplines_and_deliverables(combined_text),
        'hse_studies': _detect_hse_studies(combined_text),
        # Full HSE catalogue the planner can pick from — the UI renders every
        # entry as a checkbox and pre-selects the ones in `hse_studies`. Kept
        # here (not hardcoded in the frontend) so the master list stays
        # single-sourced in config.py.
        'available_hse_studies': list(DEFAULT_HSE_STUDIES),
        'notes': [
            'Document intelligence is generated by a deterministic keyword/pattern '
            'analyzer (no external AI API is configured). Review before finalizing.',
        ],
    }

    _augment_with_claude(intelligence, combined_text, project, user)
    _augment_with_claude_scope(intelligence, combined_text, project, user)
    return intelligence

