"""Technical proposal generation (DEC-TP-001) inside the Sales proposal workspace.

One proposal (Quote) has numbered versions (V1, V2, …). Each version is written from the selected
tender documents in the Rejlers standard and produces ONE deliverable: a PDF stored as the next
version of "<Q-ref> Technical Proposal.pdf" in the opportunity's Proposal folder and attached to
the proposal's review canvas. The Word file is kept privately and produced only on request.

Flow: request_build (durable job) → worker drafts sections from cited tender excerpts →
renders the PDF → stores and binds it → records an immutable workspace version for review.
Permissions are the existing proposal and workspace checks, re-evaluated in the worker.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
import uuid
from datetime import timedelta
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError

from .models import OpportunityProposalSourceSelection, ProposalBuildJob, QuoteTechnicalProposalVersion

TEMPLATE_VERSION = 'rejlers_authored_v1'
LEASE_SECONDS = 1800
MAX_ATTEMPTS = 3
OUTPUT_FOLDER = 'proposal'
GENERATED_OUTPUT_NAME = re.compile(r'^.+ Technical Proposal\.pdf$')


def is_generated_output(folder_key, name):
    """The proposal deliverable is an output, not tender evidence: document analysis ignores it,
    so storing a new version never makes the tender selection stale."""
    return folder_key == OUTPUT_FOLDER and bool(GENERATED_OUTPUT_NAME.match(str(name or '')))


def engine_enabled():
    return str(getattr(settings, 'SALES_PROPOSAL_ENGINE', 'legacy')).strip().lower() == 'standard'


@lru_cache(maxsize=4)
def _config(path=None):
    from proposal_engine.authored import load_authored_config
    return load_authored_config(path)


def config():
    return _config(getattr(settings, 'SALES_PROPOSAL_STANDARD_CONFIG', '') or None)


def standard_section_titles():
    """Controlled (key, title) review units of `rejlers_authored_v1` versions."""
    from proposal_engine.authored import review_units
    return tuple(review_units(config()))


# --- inputs -------------------------------------------------------------------------------

def _selection(quote, actor):
    from .proposal_workspace_service import _report

    selection = OpportunityProposalSourceSelection.objects.filter(opportunity=quote.deal, actor=actor).first()
    if selection is None or not selection.source_keys:
        raise ValidationError({'sources': 'Select the tender documents before generating.'})
    report = _report(quote.deal, actor)
    if selection.expected_snapshot != report.snapshot:
        raise ValidationError({'sources': 'The tender documents changed. Review the selection again.'})
    rows = list(report.sources.filter(source_key__in=selection.source_keys))
    if {row.source_key for row in rows} != set(selection.source_keys):
        raise ValidationError({'sources': 'One or more selected documents are no longer current.'})
    return selection, report, rows


def _tender(quote, actor, selection):
    from .technical_proposal_authoring import excerpt_pool, gather_tender, standard_pool

    cfg = config()
    requirements, sources = gather_tender(quote, actor, list(selection.source_keys))
    pool = excerpt_pool(requirements, sources, cfg['author']['max_excerpt_chars'])
    if not pool:
        raise ValidationError({'sources': 'The selected documents contain no readable tender text.'})
    return requirements, sources, pool, standard_pool(cfg['templates']['standard_content'])


# --- API operations -----------------------------------------------------------------------

def _require_enabled():
    if not engine_enabled():
        raise ValidationError({'builder': 'The standard proposal builder is not enabled.'})


def preview_plan(actor, quote_id):
    """Check before generating: what each section will be written from. No AI calls, no writes."""
    from proposal_engine.authored import section_label
    from .proposal_workspace_service import _quote
    from .technical_proposal_authoring import finding, is_primary_requirement, proposal_facts, select_excerpts

    _require_enabled()
    quote = _quote(actor, quote_id)
    selection, report, _ = _selection(quote, actor)
    requirements, sources, pool, standard = _tender(quote, actor, selection)
    cfg = config()
    facts, provenance = proposal_facts(quote, pool, cfg, quote.technical_proposal_versions.count() + 1)
    plan, findings = [], []
    for section in cfg['sections']:
        if section['kind'] in ('toc', 'divider'):
            continue
        entry = {'key': section['key'], 'label': section_label(section), 'kind': section['kind'], 'documents': []}
        if section['kind'] == 'authored':
            names = {}
            for part in section['parts']:
                for item in select_excerpts(part, pool, standard, cfg['author']['excerpt_budget_bytes']):
                    if item['kind'] == 'tender':
                        names[item['source']] = names.get(item['source'], 0) + 1
            entry['documents'] = [name for name, _ in sorted(names.items(), key=lambda pair: -pair[1])][:5]
            if not names:
                findings.append(finding('ERROR', 'SECTION_NO_SOURCE', f'{entry["label"]}: no tender text relates to this section'))
        if section['kind'] == 'evidence':
            findings.append(finding('ERROR', 'EVIDENCE_REQUIRED',
                                    f"{section['title']}: company evidence required ({', '.join(section.get('evidence') or [])})"))
        plan.append(entry)
    if 'rft_number' not in facts:
        findings.append(finding('WARN', 'META_MISSING', 'Required fact `rft_number` could not be extracted'))
    return {
        'quote_id': str(quote.pk), 'quote_ref': quote.deal.deal_code, 'snapshot': report.snapshot,
        'template_version': TEMPLATE_VERSION, 'plan': plan, 'facts': provenance, 'findings': findings,
        'tender': {'documents': len(sources),
                   'requirements': sum(1 for item in requirements if is_primary_requirement(item))},
    }


def _serialize_job(job):
    return {
        'id': str(job.pk), 'quote_id': str(job.quote_id), 'status': job.status,
        'section_key': job.section_key or None, 'attempts': job.attempts, 'error_code': job.error_code,
        'summary': job.summary,
        'result_version': job.result_version.version if job.result_version_id else None,
        'created_at': job.created_at.isoformat(), 'updated_at': job.updated_at.isoformat(),
    }


def request_build(actor, quote_id, *, section_key='', reason=''):
    """Commit a generation request (idempotent while one is active) and hand it to the worker."""
    from .proposal_workspace_service import _quote
    from .workflow import _audit

    _require_enabled()
    with transaction.atomic():
        quote = _quote(actor, quote_id, write=True, lock=True)
        active = ProposalBuildJob.objects.filter(quote=quote, status__in=('queued', 'running')).first()
        if active:
            return _serialize_job(active), False
        selection, report, _ = _selection(quote, actor)
        if section_key:
            latest = quote.technical_proposal_versions.order_by('-version').first()
            if latest is None or section_key not in {s.get('key') for s in latest.content.get('sections') or []}:
                raise ValidationError({'section_key': 'Choose an existing proposal section.'})
        try:
            with transaction.atomic():
                job = ProposalBuildJob.objects.create(
                    quote=quote, requested_by=actor, source_keys=list(selection.source_keys),
                    source_snapshot=report.snapshot, section_key=section_key or '', reason=reason or '',
                )
        except IntegrityError:  # a concurrent request won the single active slot
            return _serialize_job(ProposalBuildJob.objects.get(quote=quote, status__in=('queued', 'running'))), False
        _audit(quote.deal, actor, 'technical_proposal_build_requested', reason=reason or '',
               data={'quote_id': str(quote.pk), 'job_id': str(job.pk), 'section_key': section_key or None})
        transaction.on_commit(lambda: _dispatch(job.pk))
    return _serialize_job(job), True


def _dispatch(job_id):
    try:
        from .tasks import build_technical_proposal
        build_technical_proposal.delay(str(job_id))
    except Exception:
        pass  # the durable row stays due; the Beat dispatcher retries


def job_status(actor, quote_id, job_id):
    from .proposal_workspace_service import _quote

    quote = _quote(actor, quote_id)
    try:
        job = ProposalBuildJob.objects.select_related('result_version').get(pk=uuid.UUID(str(job_id)), quote=quote)
    except (ValueError, ProposalBuildJob.DoesNotExist):
        raise ValidationError({'job': 'This generation is unavailable.'})
    return _serialize_job(job)


def due_build_ids(limit=20):
    now = timezone.now()
    # Exhausted jobs are included so the claim step can mark them failed and free the active slot.
    return list(ProposalBuildJob.objects.filter(status__in=('queued', 'running'))
                .exclude(lease_until__gt=now).order_by('created_at').values_list('pk', flat=True)[:limit])


# --- worker -------------------------------------------------------------------------------

def _claim(job_id):
    now = timezone.now()
    with transaction.atomic():
        job = ProposalBuildJob.objects.select_for_update().filter(pk=job_id).first()
        if job is None or job.status not in ('queued', 'running') or (job.lease_until and job.lease_until > now):
            return None
        if job.attempts >= MAX_ATTEMPTS:
            job.status, job.error_code = 'failed', 'attempts_exhausted'
            job.lease_token = job.lease_until = None
            job.save(update_fields=['status', 'error_code', 'lease_token', 'lease_until', 'updated_at'])
            return None
        job.status, job.attempts = 'running', job.attempts + 1
        job.lease_token, job.lease_until = uuid.uuid4(), now + timedelta(seconds=LEASE_SECONDS)
        job.save(update_fields=['status', 'attempts', 'lease_token', 'lease_until', 'updated_at'])
        return job


def _finish(job, status, *, error_code='', summary=None, version=None):
    with transaction.atomic():
        updated = ProposalBuildJob.objects.filter(pk=job.pk, lease_token=job.lease_token).update(
            status=status, error_code=error_code, summary=summary or job.summary,
            result_version=version, lease_token=None, lease_until=None, updated_at=timezone.now(),
        )
    return bool(updated)


def run_build_job(job_id):
    """Claim and execute one generation. Safe to call repeatedly (lease + idempotent uploads)."""
    job = _claim(job_id)
    if job is None:
        return {'processed': False}
    actor = job.requested_by
    try:
        from .proposal_workspace_service import _quote

        quote = _quote(actor, job.quote_id, write=True)
        selection, report, _ = _selection(quote, actor)
        if report.snapshot != job.source_snapshot or list(selection.source_keys) != list(job.source_keys):
            _finish(job, 'failed', error_code='selection_changed')
            return {'processed': True, 'status': 'failed'}
        content, rendered = _generate(quote, actor, job, report, selection)
        try:
            with open(rendered['pdf'], 'rb') as handle:
                deliverable = _store_deliverable(quote, actor, job, handle)
            content['build_meta']['deliverable'] = deliverable
            content['build_meta']['word'] = _store_word(quote, job, rendered['docx'])
        finally:
            shutil.rmtree(Path(rendered['pdf']).parent, ignore_errors=True)
        review = _attach_for_review(quote, actor, job, deliverable)
        if review:
            content['build_findings'].append(review)
        version = _record_version(quote, actor, job, content)
    except (PermissionDenied, ValidationError) as error:
        code = 'authority_changed' if isinstance(error, PermissionDenied) else 'invalid_selection'
        _finish(job, 'failed', error_code=code)
        return {'processed': True, 'status': 'failed'}
    except Exception:
        # Leave the lease to expire; the dispatcher retries until MAX_ATTEMPTS.
        ProposalBuildJob.objects.filter(pk=job.pk, lease_token=job.lease_token).update(
            error_code='build_failed', lease_until=timezone.now(), updated_at=timezone.now())
        return {'processed': True, 'status': 'retry'}
    counts = {level: sum(1 for f in content['build_findings'] if f['severity'] == level) for level in ('ERROR', 'WARN', 'INFO')}
    _finish(job, 'succeeded', summary={'counts': counts, 'version': version.version}, version=version)
    return {'processed': True, 'status': 'succeeded', 'version': version.version}


def _generate(quote, actor, job, report, selection):
    """Draft (all sections, or one section reusing the latest version) and render the PDF."""
    from proposal_engine.authored import render, review_units
    from proposal_engine.convert import Converter
    from .technical_proposal_authoring import draft_section, finding, paragraph_records, proposal_facts

    cfg = config()
    from .technical_proposal_authoring import MAX_COMPLIANCE_ROWS, compliance_requirements, is_primary_requirement

    requirements, sources, pool, standard = _tender(quote, actor, selection)
    # Compliance and traceability cover the primary tender documents (RFT, SOW, TOR, bulletins,
    # clarifications); referenced standards and specifications remain background excerpts only.
    scope_first = re.compile(r'terms of reference|\btor\b|scope of work|\bsow\b', re.I)
    rft_doc = re.compile(r'request for tender|\brft\b', re.I)
    requirements = sorted(compliance_requirements(item for item in requirements if is_primary_requirement(item)),
                          key=lambda item: (not scope_first.search(item.get('source_file') or ''),
                                            bool(rft_doc.search(item.get('source_file') or ''))))[:MAX_COMPLIANCE_ROWS]
    requirement_ids = {item['id'] for item in requirements}
    next_number = (QuoteTechnicalProposalVersion.objects.filter(quote=quote).aggregate(value=Max('version'))['value'] or 0) + 1
    facts, provenance = proposal_facts(quote, pool, cfg, next_number)
    latest = QuoteTechnicalProposalVersion.objects.filter(quote=quote).order_by('-version').first()
    prior = {s['key']: s for s in (latest.content.get('sections') or [])} if (
        job.section_key and latest and latest.content.get('template_version') == TEMPLATE_VERSION) else {}

    from .proposal_evidence import evidence_uploads, materialize

    evidence = evidence_uploads(quote.deal, [s['key'] for s in cfg['sections'] if s['kind'] == 'evidence'])
    drafted, findings, excerpts_used = {}, [], set()
    for section in cfg['sections']:
        if section['kind'] not in ('authored', 'evidence', 'not_applicable'):
            continue
        if evidence.get(section['key']):
            # Enclosed company evidence replaces the placeholder; checked on every build.
            names = ', '.join(upload.name for upload in evidence[section['key']])
            drafted[section['key']] = {'blocks': []}
            findings.append(finding('INFO', 'EVIDENCE_ATTACHED', f"{section['title']}: {names}") | {'section': section['key']})
            continue
        previous = prior.get(section['key'])
        if previous and section['key'] != job.section_key and section['kind'] != 'evidence':
            drafted[section['key']] = {'blocks': previous.get('blocks') or []}
            findings.extend(previous.get('findings') or [])
            excerpts_used |= {c for b in previous.get('blocks') or [] for c in b.get('citations') or []}
            continue
        outcome = draft_section(section, pool, standard, facts, cfg,
                                reviewer_note=job.reason if section['key'] == job.section_key else '')
        drafted[section['key']] = {'blocks': outcome['blocks']}
        section_findings = outcome['findings']
        for item in section_findings:
            item.setdefault('section', section['key'])
        findings.extend(section_findings)
        excerpts_used |= outcome['cited']

    # A requirement is addressed where a section cites it directly, or cites a tender excerpt
    # that contains the requirement's text.
    pool_text = {item['id']: ' '.join(item['text'].split()).casefold() for item in pool}
    requirement_text = {item['id']: ' '.join(item['requirement_text'].split()).casefold() for item in requirements}
    traceability = {}
    for key, value in drafted.items():
        for block in value['blocks']:
            for citation in block.get('citations') or []:
                if citation in requirement_ids:
                    traceability.setdefault(citation, set()).add(key)
                    continue
                window = pool_text.get(citation)
                if window:
                    for rid, text in requirement_text.items():
                        if text and text[:200] in window:
                            traceability.setdefault(rid, set()).add(key)
    unaddressed = len(requirement_ids - set(traceability))
    if unaddressed:
        findings.append(finding('WARN', 'REQUIREMENT_NOT_ADDRESSED',
                                f'{unaddressed} of {len(requirement_ids)} tender requirements are not referenced '
                                f'by any section; see the compliance matrix'))
    if 'rft_number' not in facts:
        findings.append(finding('WARN', 'META_MISSING', 'Required fact `rft_number` could not be extracted'))

    with tempfile.TemporaryDirectory(prefix='proposal_build_') as workdir:
        converter = Converter(Path(workdir) / 'cache', getattr(settings, 'SALES_PROPOSAL_CONVERTER', 'auto'))
        attachments = {key: materialize(uploads, Path(workdir) / 'evidence' / key)
                        for key, uploads in evidence.items() if uploads}
        try:
            rendered = render(cfg, facts, drafted, requirements, traceability, converter, workdir,
                              attachments=attachments)
        finally:
            converter.close()
        findings.extend(finding(s, c, m) for s, c, m in rendered['findings'])
        # Keep the files beyond the temporary directory for storage.
        keep = Path(tempfile.mkdtemp(prefix='proposal_out_'))
        pdf, docx = keep / 'proposal.pdf', keep / 'proposal.docx'
        pdf.write_bytes(Path(rendered['pdf']).read_bytes())
        docx.write_bytes(Path(rendered['docx']).read_bytes())

    pool_by_id = {item['id']: item for item in pool + standard}
    labels = dict(review_units(cfg))
    sections = []
    for key, title in review_units(cfg):
        blocks = drafted.get(key, {}).get('blocks') or []
        paragraphs = paragraph_records(key, blocks, requirement_ids)
        kind = next(s['kind'] for s in cfg['sections'] if s['key'] == key)
        if kind in ('cover', 'letter', 'compliance') and not paragraphs:
            paragraphs = [{'id': f'PAR-{key}', 'kind': 'generated', 'text': f'{title} (generated from the template)',
                           'requirement_ids': [], 'evidence_ids': []}]
        previous = prior.get(key)
        sections.append({
            'key': key, 'title': title, 'kind': kind, 'blocks': blocks, 'paragraphs': paragraphs, 'items': paragraphs,
            'findings': [f for f in findings if f.get('section') == key],
            'pages': rendered['section_pages'].get(key), 'requirement_ids': [],
            'attachments': [{'id': f'radai-{u.pk}', 'name': u.name} for u in evidence.get(key) or []],
            'approved': bool(previous and previous.get('approved') and key != job.section_key),
            'status': 'approved' if previous and previous.get('approved') and key != job.section_key else 'draft',
        })
    registry = {cid: {'kind': pool_by_id[cid]['kind'], 'source': pool_by_id[cid]['source'],
                      'location': pool_by_id[cid]['location'], 'excerpt': pool_by_id[cid]['text'][:600]}
                for cid in excerpts_used if cid in pool_by_id}
    matrix = []
    for requirement in requirements:
        where = sorted(traceability.get(requirement['id']) or [])
        matrix.append({'requirement_id': requirement['id'], 'proposal_section': where[0] if where else '',
                       'sections': where, 'compliance_status': 'Addressed' if where else 'To be confirmed',
                       'evidence_reference': [requirement['id']] if where else [], 'review_note': ''})
    content = {
        'title': f"{quote.deal.deal_code} Technical Proposal", 'template_version': TEMPLATE_VERSION,
        'client_name': quote.client.company_name, 'opportunity_title': quote.deal.deal_name,
        'opportunity_reference': quote.deal.deal_code, 'proposal_number': quote.quote_number,
        'revision': next_number, 'issue_date': facts['date'],
        'analysis_status': report.status, 'source_snapshot': job.source_snapshot,
        'selected_source_keys': list(job.source_keys),
        'requirement_coverage': {'requirements': len(requirement_ids), 'addressed': len(traceability),
                                 'documents': len(sources)},
        'requirement_matrix': requirements, 'normalized_requirement_matrix': [], 'requirement_normalization': {},
        'compliance_matrix': matrix,
        'traceability_matrix': [{'requirement_id': rid, 'sections': sorted(keys), 'result': 'mapped'}
                                for rid, keys in traceability.items()],
        'evidence_registry': registry, 'publication_validation': {'ready': False, 'blockers': []},
        'sections': sections, 'document_registry': [], 'amendments': [],
        'requirements_included': len(requirements), 'requirements_truncated': False,
        'build_meta': {'engine': 'proposal_engine', 'standard': TEMPLATE_VERSION, 'job_id': str(job.pk),
                       'facts': provenance, 'labels': labels},
        'build_findings': [{k: v for k, v in f.items() if k != 'section'} | ({'section': f['section']} if f.get('section') else {})
                           for f in findings],
        'build_inventory': [],
    }
    return content, {'pdf': pdf, 'docx': docx}


def _store_deliverable(quote, actor, job, handle):
    """Store the PDF as the next version of the proposal's single deliverable document."""
    from .private_attachments import upload_private_file

    name = config()['deliverable']['name'].format(quote_ref=quote.deal.deal_code)
    data = handle.read()
    handle.seek(0)
    digest = hashlib.sha256(data).hexdigest()
    request_id = uuid.uuid5(uuid.NAMESPACE_URL, f'proposal_build:{job.pk}:deliverable:{digest}')
    item, _ = upload_private_file(quote.deal, actor, OUTPUT_FOLDER, File(handle, name=name), request_id)
    return {'file_id': item['id'], 'name': name, 'sha256': digest, 'size': len(data),
            'document_version': item.get('version')}


def _store_word(quote, job, path):
    """Keep the Word master privately (not in the Proposal folder); served only on request."""
    from .private_attachments import _storage

    storage, _ = _storage()
    data = Path(path).read_bytes()
    with open(path, 'rb') as handle:
        name = storage.save(f'proposal-word/{quote.pk}/{job.pk}.docx', File(handle))
    return {'storage_name': name, 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}


def _attach_for_review(quote, actor, job, deliverable):
    """Attach the new PDF to the proposal's review canvas (comments, internal review)."""
    from .proposal_review import _current, review_command
    from .models import Quote
    from .technical_proposal_authoring import finding

    try:
        fresh = Quote.objects.get(pk=quote.pk)
        current = _current(fresh)
        review_command(fresh.pk, actor, 'bind', {
            'request_id': str(uuid.uuid5(uuid.NAMESPACE_URL, f'proposal_build:{job.pk}:review')),
            'file_id': deliverable['file_id'],
            'expected_document_id': str(current.pk) if current else None,
            'expected_quote_updated_at': fresh.updated_at.isoformat(),
        })
        return None
    except Exception as error:  # review stays possible manually; never fail the generation
        detail = getattr(error, 'detail', None) or str(error)
        return finding('WARN', 'REVIEW_NOT_ATTACHED', f'The PDF could not be attached for review automatically: {detail}')


def _record_version(quote, actor, job, content):
    from .proposal_workspace import _validate_content
    from .proposal_workspace_service import _digest
    from .workflow import _audit

    with transaction.atomic():
        locked = type(quote).objects.select_for_update(of=('self',)).get(pk=quote.pk)
        next_version = (QuoteTechnicalProposalVersion.objects.filter(quote=locked)
                        .aggregate(value=Max('version'))['value'] or 0) + 1
        content['revision'] = next_version
        content = _validate_content(content)
        version = QuoteTechnicalProposalVersion.objects.create(
            quote=locked, version=next_version, content=content, content_sha256=_digest(content),
            created_by=actor, reason=job.reason or ('section rebuild' if job.section_key else 'generated from tender'),
        )
        _audit(locked.deal, actor, 'technical_proposal_workspace_generated', data={
            'quote_id': str(locked.pk), 'workspace_version': next_version, 'job_id': str(job.pk),
            'template_version': TEMPLATE_VERSION, 'section_key': job.section_key or None,
            'errors': sum(1 for f in content['build_findings'] if f['severity'] == 'ERROR')})
    return version


def stored_output(actor, quote_id, version_row, kind):
    """Stream the version's PDF deliverable ('client_pdf') or its private Word file ('master_docx')."""
    from .opportunity_workspace import download_workspace_file
    from .private_attachments import _storage
    from .proposal_workspace_service import _quote

    quote = _quote(actor, quote_id)
    meta = version_row.content.get('build_meta') or {}
    if kind == 'client_pdf':
        deliverable = meta.get('deliverable')
        if not deliverable:
            raise ValidationError({'workspace': 'This version has no proposal PDF.'})
        stream, _ = download_workspace_file(quote.deal, actor, OUTPUT_FOLDER, deliverable['file_id'])
        return stream, f"{quote.deal.deal_code} Technical Proposal V{version_row.version}.pdf"
    word = meta.get('word')
    if not word:
        raise ValidationError({'workspace': 'This version has no Word file.'})
    from .opportunity_workspace import require_access
    require_access(actor, quote.deal, 'export')
    storage, _ = _storage()
    return storage.open(word['storage_name'], 'rb'), f"{quote.deal.deal_code} Technical Proposal V{version_row.version}.docx"
