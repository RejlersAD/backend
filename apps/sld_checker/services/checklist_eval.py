"""
AI-SLD-01 checklist evaluation — computed LIVE on every GET.

Simplified mirror of apps.pid_verification_v2.services.checklist_ai02:
  1. applicability override (SldChecklistResult, engineer) wins;
  2. missing input (ref_inputs vs present inputs) → Not Verifiable;
  3. linked finding (RULE_TO_ITEMS) → Non-Conformance (critical/major)
     else Query;
  4. applicable Automated item with no finding → OK;
  5. Engineer-Led → Query.

Nothing is persisted here; only engineer state lives in SldChecklistResult.
"""
from __future__ import annotations

import logging

from ..models import SldChecklistItem, SldChecklistResult, SldChecklistTemplate, SldFinding
from .checklist_map import RULE_TO_ITEMS

logger = logging.getLogger(__name__)

# (needle in lowercase ref_inputs, input key, display name)
INPUT_KEYWORDS = [
    ('sld', 'sld', 'SLD'),
    ('single line', 'sld', 'SLD'),
    ('equipment list', 'equipment_list', 'Equipment List'),
    ('load list', 'load_list', 'Load List'),
    ('legend', 'legend', 'Legend'),
    ('cable schedule', 'cable_schedule', 'Cable schedule'),
    ('relay', 'relay_settings', 'Relay settings'),
    ('protection', 'relay_settings', 'Relay settings'),
    ('short-circuit', 'sc_study', 'Short-circuit study'),
    ('short circuit', 'sc_study', 'Short-circuit study'),
    ('sc study', 'sc_study', 'Short-circuit study'),
    ('load study', 'load_study', 'Load study'),
    ('load flow', 'load_study', 'Load study'),
    ('design basis', 'design_basis', 'Design basis'),
]

ENGINEER_LED_MODES = {'Engineer-Led'}


def default_template():
    tpl = SldChecklistTemplate.objects.filter(code='AI-SLD-01', is_default=True).first()
    if tpl is None:
        tpl = SldChecklistTemplate.objects.filter(code='AI-SLD-01').first()
    return tpl


def _inputs_present(analysis):
    from apps.electrical_comparison.models import SldAnalysisFile, SldProjectSettings
    kinds = set(SldAnalysisFile.objects.filter(project=analysis.project)
                .values_list('kind', flat=True))
    st = SldProjectSettings.objects.filter(project=analysis.project).first()
    return {
        'sld': 'sld' in kinds,
        'equipment_list': 'equipment' in kinds,
        'load_list': 'load' in kinds,
        'legend': bool(st and st.legend),
        'cable_schedule': False,
        'relay_settings': False,
        'sc_study': False,
        'load_study': False,
        'design_basis': False,
    }


def _missing_inputs(ref_inputs, present):
    missing = []
    text = (ref_inputs or '').lower()
    for needle, key, label in INPUT_KEYWORDS:
        if needle not in text:
            continue
        if present.get(key, False):
            continue
        if label not in missing:
            missing.append(label)
    return missing


def evaluate(analysis, page=0, template=None):
    """{items: [{no, applicability, ai_result, engineer_verification,
    remarks, evidence, finding_numbers, finding_ids, missing_inputs,
    xlsx_row, severity, mode, check_point, section…}]} or None when no
    template is imported."""
    template = template or default_template()
    if template is None:
        return None

    present = _inputs_present(analysis)
    findings = list(SldFinding.objects.filter(analysis_id=analysis.id))
    findings_by_item = {}
    for f in findings:
        for no in (f.checklist_items or RULE_TO_ITEMS.get(f.rule_id, [])):
            findings_by_item.setdefault(no, []).append(f)

    states = {(r.item_id): r for r in
              SldChecklistResult.objects.filter(analysis_id=analysis.id, page=page)}

    items = []
    for it in SldChecklistItem.objects.filter(template=template):
        st = states.get(it.id)
        applicability = st.applicability if st else 'Y'
        applicability_by = st.applicability_by if st else 'ai'
        linked = findings_by_item.get(it.no, [])
        missing = _missing_inputs(it.ref_inputs, present)

        if applicability != 'Y':
            ai_result, evidence = None, ''
        elif missing:
            ai_result = 'Not Verifiable'
            evidence = 'Missing: ' + ', '.join(missing)
        elif linked:
            worst = min(linked, key=lambda f: {'critical': 0, 'major': 1, 'minor': 2}[f.severity])
            ai_result = ('Non-Conformance'
                         if worst.severity in ('critical', 'major')
                         else 'Query')
            evidence = '; '.join(f'{f.number} {f.title}' for f in linked[:5])
        elif it.mode in ENGINEER_LED_MODES:
            ai_result = 'Query'
            evidence = 'Engineer-Led item — AI provides no verdict.'
        else:
            ai_result = 'OK'
            evidence = 'Checked; no linked findings.'

        items.append({
            'no': it.no, 'section_no': it.section_no, 'section_title': it.section_title,
            'sub_heading': it.sub_heading, 'check_point': it.check_point,
            'mode': it.mode, 'ai_task': it.ai_task, 'ref_inputs': it.ref_inputs,
            'evidence_required': it.evidence_required, 'severity': it.severity,
            'xlsx_row': it.xlsx_row, 'order': it.order,
            'applicability': applicability, 'applicability_by': applicability_by,
            'ai_result': ai_result, 'evidence': evidence,
            'finding_ids': [str(f.id) for f in linked],
            'finding_numbers': [f.number for f in linked],
            'missing_inputs': missing,
            'engineer_verification': st.engineer_verification if st else None,
            'remarks': st.remarks if st else '',
            'verified_by': (st.verified_by.get_full_name() or st.verified_by.username)
                           if st and st.verified_by else '',
            'verified_at': st.verified_at if st else None,
        })
    return {'template': {'code': template.code, 'version': template.version,
                         'title': template.title},
            'items': items}
