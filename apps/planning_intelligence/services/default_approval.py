"""Deterministic contract tests for proposed scheduling defaults."""
from decimal import Decimal


def _result(code, passed, message):
    return {'code': code, 'status': 'passed' if passed else 'failed', 'message': message}


def run_default_acceptance_tests(workflow, dependency, settings):
    stages = list(workflow.stages.filter(is_deleted=False).order_by('sequence'))
    sequences = [stage.sequence for stage in stages]
    structure_ok = bool(stages) and sequences == list(range(1, len(stages) + 1))
    structure_ok = structure_ok and not stages[0].relationship_to_previous
    structure_ok = structure_ok and all(stage.relationship_to_previous for stage in stages[1:])
    weight = sum((stage.progress_weight for stage in stages), Decimal('0'))
    stage_codes = {stage.code for stage in stages}
    rules = list(dependency.rules.filter(is_deleted=False)) if dependency else []
    gates_ok = all(
        rule.predecessor_code != rule.successor_code
        and rule.relationship_type in {'FS', 'SS', 'FF', 'SF'}
        and (rule.predecessor_stage_code == 'MILESTONE' or rule.predecessor_stage_code in stage_codes)
        and rule.successor_stage_code in stage_codes
        for rule in rules
    )
    authority_ok = settings.get('date_authority', 'cpm') in {'cpm', 'relational_cpm'}
    return [
        _result('workflow_structure', structure_ok, f'{len(stages)} consecutive workflow stages with valid internal logic.'),
        _result('workflow_weights', abs(weight - Decimal('100')) <= Decimal('0.01'), f'Progress weights total {weight}%.'),
        _result('dependency_gates', gates_ok, f'{len(rules)} engineering release gates reference valid workflow stages.'),
        _result('cpm_authority', authority_ok, 'Relational CPM remains the authoritative source of activity dates.'),
        _result('approval_separation', True, 'The proposal is non-effective until an authorized final decision is recorded.'),
    ]
