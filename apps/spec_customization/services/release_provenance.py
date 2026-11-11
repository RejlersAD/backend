"""Conservative preview provenance and reviewed-code consumption."""
from ..models import PipingClassComponent
from .component_matcher import normalize_size


def bindings(job):
    if not job.reference_workbook_set_id:
        return {}
    ws = job.reference_workbook_set
    decisions = list(job.match_decisions.filter(workbook_set=ws))
    output = {}
    for component in PipingClassComponent.objects.filter(piping_class__job=job).select_related('piping_class'):
        pc = component.piping_class
        size = component.size_from
        if component.size_to and normalize_size(component.size_to) != normalize_size(size):
            size = f'{size}..{component.size_to}'
        names = [f'{component.sub_type} {component.component_type}'.strip(), component.sub_type, component.component_type]
        name = next((n for n in names if ws.rules.filter(pdf_component_name__iexact=n).exists()), names[0]).upper()
        matches = [decision for decision in decisions if
            decision.pdf_component_name == name and decision.nominal_size == size and
            decision.pressure_rating == pc.pressure_rating and
            decision.material_grade == (component.material_standard or pc.material_grade) and
            decision.sub_type == component.sub_type and decision.end_connection == component.end_connection]
        if len(matches) == 1:
            decision = matches[0]
            output[str(component.pk)] = {'id': str(decision.pk), 'reference_set_id': str(ws.pk),
                'approved': decision.review_decision in ('approved', 'remapped') and bool(decision.final_commodity_code)
                            and decision.state not in ('ineligible', 'invalid_reference'),
                'commodity_code': decision.final_commodity_code, 'state': decision.state}
            if decision.discriminators_matched.get('pcmc'):
                from .pcmc_matching import pcmc_code_format_issue
                if pcmc_code_format_issue(decision.final_commodity_code):
                    output[str(component.pk)]['approved'] = False
                selected = next((c for c in decision.candidates if c.get('candidate_id') == decision.discriminators_matched.get('selected_candidate_id')), {})
                output[str(component.pk)].update(pcmc=True, industry_commodity_code=selected.get('industry_commodity_code', ''))
            locator = decision.discriminators_matched.get('source_reference')
            if locator:
                output[str(component.pk)]['source_reference'] = locator
            elif component.source_reference:
                output[str(component.pk)]['source_reference'] = component.source_reference
    return output


def apply_reference_bindings(job, preview):
    # Enumerate the existing CAT generator, not a second preview, to recover
    # the established SPEC/CAT join keys and identify collisions explicitly.
    from .exporters.workbook_preview import _enumerate_cat_rows
    approved = bindings(job)
    joins = {}
    for _, _, source, cells in (_enumerate_cat_rows(job) if approved else []):
        key = (source.get('class_id'), cells.get('IndustryCommodityCode') or cells.get('CommodityCode'))
        joins.setdefault(key, set()).add(source.get('component_id'))
    classes = {str(row.pk): row for row in job.piping_classes.all()}
    components = {str(row.pk): row for row in PipingClassComponent.objects.filter(piping_class__job=job)}
    for sheet in preview['sheets']:
        for row in sheet['rows']:
            source = row.get('source') or {}
            origins = row.setdefault('provenance', {key: 'reference' if row['row_key'].startswith('tpl:') else 'inferred' for key in row['cells']})
            pc = classes.get(source.get('class_id'))
            component = components.get(source.get('component_id'))
            if component and sheet['name'] == 'PipingCommodityFilter':
                row['cells']['CommodityCode'] = component.pms_commodity_code
                row['cells']['PMSCommodityCode'] = component.pms_commodity_code
                row['cells']['CommodityCodeStatus'] = ('Unreadable' if component.source_read_status not in ('blank', 'read')
                    else 'Read from PMS' if component.pms_commodity_code.strip() else 'Blank in PMS')
                row['cells']['ConfirmedPCMCCode'] = ''
                origins.update({'CommodityCode': 'extracted', 'PMSCommodityCode': 'extracted',
                                'CommodityCodeStatus': 'extracted', 'ConfirmedPCMCCode': 'inferred'})
                source['reference'] = component.source_reference
            facts = {'SpecName': getattr(pc, 'class_code', ''), 'MaterialGrade': getattr(pc, 'material_grade', ''),
                     'Description': getattr(component, 'description', ''), 'EndConnection': getattr(component, 'end_connection', ''),
                     'PressureRating': getattr(pc, 'pressure_rating', '')}
            for key, value in row['cells'].items():
                if value and key in facts and str(value) == str(facts[key]) and origins.get(key) != 'default':
                    origins[key] = 'extracted'
            if row['row_key'].startswith('unrouted:'):
                origins.update({key: 'extracted' for key in row['cells']})
            for key in row.get('overridden', []):
                origins[key] = 'override'
            component_id = source.get('component_id')
            if not component_id:
                ids = set()
                for key, value in row['cells'].items():
                    if 'CommodityCode' in key:
                        ids.update(joins.get((source.get('class_id'), value), set()))
                if len(ids) == 1:
                    component_id = next(iter(ids))
                    source['component_id'] = component_id
            decision = approved.get(component_id)
            if decision:
                decision = dict(decision)
                component = components.get(component_id)
                row_sizes = [row['cells'].get(key) for key in ('FirstSizeFrom', 'FirstSizeTo', 'Npd[1]', 'Npd[1]:Primary') if row['cells'].get(key)]
                if component and row_sizes:
                    from .pcmc_matching import size_number
                    low, high = size_number(component.size_from), size_number(component.size_to or component.size_from)
                    if low is None or high is None or any(size_number(value) is None or not low <= size_number(value) <= high for value in row_sizes):
                        decision['approved'] = False
                row['match_decision'] = decision
                if decision.get('source_reference'):
                    source['reference'] = decision['source_reference']
                if decision['approved']:
                    if sheet['name'] == 'PipingCommodityFilter':
                        row['cells']['ConfirmedPCMCCode'] = decision['commodity_code']
                        origins['ConfirmedPCMCCode'] = 'reference'
                    elif component:
                        for key in ('IndustryCommodityCode', 'CommodityCode', 'ContractorCommodityCode'):
                            if key in row['cells']:
                                row['cells'][key] = (decision.get('industry_commodity_code', '') if key == 'IndustryCommodityCode' else decision['commodity_code']) if decision.get('pcmc') else decision['commodity_code']
                                origins[key] = 'reference'
                    old_codes = {key[1] for key, ids in joins.items() if ids == {component_id}}
                    old_codes.update(row['cells'].get(key) for key in ('CommodityCode', 'IndustryCommodityCode', 'ContractorCommodityCode') if row['cells'].get(key))
                    for key, value in row['cells'].items():
                        if not decision.get('pcmc') and sheet['name'] != 'PipingCommodityFilter' and 'CommodityCode' in key and value in old_codes and key not in row.get('overridden', []):
                            row['cells'][key] = decision['commodity_code']
                            origins[key] = 'reference'
            row['origin'] = 'override' if row.get('overridden') else 'reference' if 'reference' in origins.values() else 'extracted' if origins and set(origins.values()) == {'extracted'} else 'default' if origins and set(origins.values()) == {'default'} else 'inferred'
    preview['reference_set_id'] = str(job.reference_workbook_set_id) if job.reference_workbook_set_id else None
    return preview
