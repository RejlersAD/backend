"""End-to-end build: scan → plan → extract → validate → render → (compare) → report."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .config import load_config
from .convert import Converter
from .extract import extract_facts
from .plan import build_plan
from .scan import open_source, scan
from .plan import plan_summary
from .validate import ORDER, validate, write_report


def _safe(text):
    return re.sub(r'[^A-Za-z0-9_.-]+', '-', text).strip('-') or 'proposal'


def build_proposal(sources, out_dir, *, config_path=None, quote_ref=None, reference_pdf=None,
                   converter_backend='auto', render=True, review_render=True):
    """Build the master DOCX, client PDF binder, facts.json and build_report.md.

    `sources` – directories or ZIPs (e.g. 03_Proposal and 04_Internal).
    `quote_ref` – the bid being produced. When omitted it is taken from the documents (majority vote).
    `review_render` – also render the master DOCX to PDF for visual review (skip on servers).
    Returns output paths, finding counts, findings, plan summary, facts and the optional comparison.
    """
    config = load_config(config_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / '_work'
    work.mkdir(exist_ok=True)
    roots, temps = [], []
    try:
        for source in sources:
            root, temp = open_source(source, workdir=work)
            roots.append((root, '') if temp else (root, root.name))
            if temp:
                temps.append(temp)
        inventory = scan(roots, config, quote_ref)
        plan, selections = build_plan(config, inventory)
        facts = extract_facts(config, selections, inventory['files'], quote_ref)
        quote = quote_ref or (facts['facts'].get('quote_ref') or {}).get('value')
        if not quote_ref and quote:
            # Re-scan path names for foreign bids now that the bid reference is known.
            inventory = scan(roots, config, quote)
            plan, selections = build_plan(config, inventory)
        findings = validate(config, inventory, plan, selections, facts, quote)
        title = f"{quote or 'UNKNOWN'} Technical Proposal Rev {(facts['facts'].get('revision') or {}).get('value', '0')}"
        config['_title'] = title
        outputs, comparison, files = {}, None, {}
        if render:
            from .binder import build_binder
            from .master import build_master

            converter = Converter(work / 'cache', converter_backend)
            try:
                binder_pdf = out_dir / f'{_safe(title)} - Client.pdf'
                page_map = build_binder(plan, config, converter, work, binder_pdf, findings)
                master_docx = out_dir / f'{_safe(title)} - Master.docx'
                build_master(plan, config, facts, selections, inventory['files'], master_docx, findings)
                outputs['Client PDF (compiled binder)'] = binder_pdf.name
                outputs['Master DOCX (editable)'] = master_docx.name
                files.update(client_pdf=str(binder_pdf), master_docx=str(master_docx))
                try:
                    if not review_render:
                        raise LookupError('skipped')
                    master_pdf = out_dir / '_work' / 'master_render.pdf'
                    shutil.copyfile(converter.to_pdf(master_docx, update_fields=True), master_pdf)
                    outputs['Master DOCX rendered for review'] = str(master_pdf.relative_to(out_dir))
                except LookupError:
                    pass
                except Exception as error:
                    findings.append(('WARN', 'MASTER_RENDER_FAILED', str(error)))
                (out_dir / 'page_map.json').write_text(json.dumps(page_map, indent=1), encoding='utf-8')
                outputs['Page map'] = 'page_map.json'
                if reference_pdf:
                    from .compare import compare

                    comparison = compare(binder_pdf, reference_pdf, plan)
            finally:
                converter.close()
        (out_dir / 'facts.json').write_text(json.dumps(facts, indent=1, ensure_ascii=False), encoding='utf-8')
        outputs['Facts with provenance'] = 'facts.json'
        counts = write_report(out_dir / 'build_report.md', config=config, inventory=inventory, plan=plan,
                              facts=facts, findings=findings, outputs=outputs, comparison=comparison,
                              quote_ref=quote)
        files['build_report'] = str(out_dir / 'build_report.md')
        return {'outputs': outputs, 'counts': counts, 'comparison': comparison, 'quote_ref': quote,
                'report': str(out_dir / 'build_report.md'), 'files': files,
                'findings': [{'severity': s, 'code': c, 'message': m}
                             for s, c, m in sorted(set(findings), key=lambda f: (ORDER[f[0]], f[1], f[2]))],
                'plan': plan_summary(plan), 'facts': facts['facts'],
                'table_counts': {name: len(rows) for name, rows in facts['tables'].items()},
                'title': title}
    finally:
        for temp in temps:
            shutil.rmtree(temp, ignore_errors=True)
