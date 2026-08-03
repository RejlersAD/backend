"""Quick sanity check: are activities landing under the WBS Builder's tree?"""
from apps.planning_intelligence.models import PlanningGeneration
from apps.planning_intelligence.services.activity_generator import build_activities
from apps.planning_intelligence.services.wbs_generator import build_wbs
from collections import Counter


def report(gens, label):
    print(f'\n===== {label} =====')
    for g in gens:
        wbs = g.wbs or []
        acts = g.activities or []
        wbs_codes = {n['code']: n for n in wbs}
        missing = [a for a in acts if a.get('wbs_code') not in wbs_codes]
        at_root = [a for a in acts if a.get('wbs_code') == '1']
        dist = Counter(
            (a.get('wbs_code'), wbs_codes.get(a.get('wbs_code'), {}).get('name', '?'))
            for a in acts
        )
        print(f'--- Gen {g.id} (proj={g.project_id}) wbs_nodes={len(wbs)} activities={len(acts)}')
        print(f'  missing_wbs={len(missing)}  at_project_root_"1"={len(at_root)}')
        print('  distribution:')
        for (code, name), n in sorted(dist.items()):
            nm = (name or '?')[:45]
            print(f'    {code:<10} {nm:<45} {n:>4}')


gens = list(PlanningGeneration.objects.order_by('-created_at')[:3])
report(gens, 'STORED (pre-fix generations)')

print('\n===== SIMULATED RE-RUN (with new alignment) =====')
for g in gens:
    if not g.intelligence:
        print(f'Gen {g.id}: no intelligence snapshot, skipping simulation')
        continue
    new_wbs = build_wbs(g.project, g.intelligence)
    result = build_activities(g.project, new_wbs, g.intelligence)
    new_acts = result['activities']
    wbs_codes = {n['code']: n for n in new_wbs}
    missing = [a for a in new_acts if a.get('wbs_code') not in wbs_codes]
    at_root = [a for a in new_acts if a.get('wbs_code') == '1']
    dist = Counter(
        (a.get('wbs_code'), wbs_codes.get(a.get('wbs_code'), {}).get('name', '?'))
        for a in new_acts
    )
    print(f'--- Gen {g.id} (proj={g.project_id}) wbs={len(new_wbs)} activities={len(new_acts)}')
    print(f'  missing_wbs={len(missing)}  at_project_root_"1"={len(at_root)}')
    for (code, name), n in sorted(dist.items()):
        nm = (name or '?')[:45]
        print(f'    {code:<10} {nm:<45} {n:>4}')
