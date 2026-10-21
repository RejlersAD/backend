"""Controlled release commands; no external delivery or inferred approval grants."""
import hashlib
import io
import json
import zipfile
from contextvars import ContextVar
from functools import wraps
from pathlib import Path
from xml.etree import ElementTree

from django.conf import settings
from django.db import transaction
from django.db.models import F

from ..models import PaperSpecExtractionJob, PipingClassComponent, WorkbookCellOverride
from ..release_models import ValidationRun, SpecRelease, SpecAuditEvent
from .completeness import extraction_completeness
from .reference_intelligence import file_sha256, reference_hash
from .exporters import smartplant_config as cfg
from .exporters.workbook_preview import build_preview

_actor = ContextVar('s3d_audit_actor', default=None)


def digest(value):
    if not isinstance(value, bytes):
        value = json.dumps(value, sort_keys=True, separators=(',', ':'), default=str, ensure_ascii=False).encode()
    return hashlib.sha256(value).hexdigest()


def audit(job, event, actor=None, **details):
    return SpecAuditEvent.objects.create(job_identity=job.pk, event=event, author=actor or _actor.get(), details=details)


def invalidate(job_id, event, actor=None, **details):
    """Caller holds the job lock. Frozen evidence is retained, never rewritten."""
    job = PaperSpecExtractionJob.objects.get(pk=job_id)
    PaperSpecExtractionJob.objects.filter(pk=job_id).update(override_revision=F('override_revision') + 1)
    frozen = job.spec_releases.filter(state='frozen').first()
    if frozen and not frozen.successors.filter(state='invalidated').exists():
        SpecRelease.objects.create(job=job, state='invalidated', parent=frozen,
                                   manifest=frozen.manifest, author=actor or _actor.get(), blockers=[event])
    audit(job, event, actor, **details)


def mutation(event):
    """Serialize existing editing commands against validation/freeze/export."""
    def decorate(view):
        @wraps(view)
        def wrapped(request, job_id, *args, **kwargs):
            from ..views import _get_accessible_job
            with transaction.atomic():
                job = _get_accessible_job(request, job_id)
                PaperSpecExtractionJob.objects.select_for_update().get(pk=job.pk)
                token = _actor.set(request.user)
                try:
                    response = view(request, job_id, *args, **kwargs)
                    if response.status_code < 300:
                        if event != 'chatbot' or not request.data.get('preview_only', False):
                            invalidate(job_id, event, request.user)
                    if response.status_code >= 400:
                        transaction.set_rollback(True)
                    return response
                finally:
                    _actor.reset(token)
        return wrapped
    return decorate


def snapshot(job):
    """Exclude keys/provider credentials; retain engineering facts and revisions."""
    previews = {kind: build_preview(job, kind) for kind in ('spec', 'cat')}
    source = list(job.piping_classes.order_by('id').values())
    components = list(PipingClassComponent.objects.filter(piping_class__job=job).order_by('id').values())
    overrides = list(WorkbookCellOverride.objects.filter(job=job).order_by('workbook', 'sheet_name', 'row_key', 'column_name').values())
    decisions = list(job.match_decisions.order_by('id').values())
    templates = {kind: digest(Path(path).read_bytes()) for kind, path in [('spec', cfg.SPEC_TEMPLATE_PATH), ('cat', cfg.CAT_TEMPLATE_PATH)]}
    generator = {name: digest(Path(__file__).with_name('exporters').joinpath(name).read_bytes())
                 for name in ('smartplant_config.py', 'workbook_preview.py', 'smartplant_exporter.py')}
    errors = []
    document_hash = job.document.sha256_hash
    stored_document_hash = ''
    try:
        stored_document_hash = file_sha256(job.document.file)
    except Exception:
        errors.append('Source document bytes are unavailable for hashing.')
    if not document_hash or len(document_hash) != 64:
        errors.append('Source document SHA-256 is missing.')
    reference = job.reference_workbook_set
    actual_hashes = {}
    if reference:
        for kind in ('match', 'spec', 'cat'):
            try:
                actual_hashes[kind] = file_sha256(getattr(reference, f'{kind}_file'))
                if actual_hashes[kind] != getattr(reference, f'{kind}_file_hash'):
                    errors.append(f'{kind.upper()} reference integrity mismatch.')
            except Exception:
                errors.append(f'{kind.upper()} reference file is unavailable.')
    manifest = {
        'document_sha256': document_hash, 'stored_document_sha256': stored_document_hash,
        'job_id': str(job.pk), 'reference_set_id': str(reference.pk) if reference else None,
        'reference_set_hash': reference_hash(reference) if reference else '',
        'template_hash': digest(templates), 'generator_hash': digest(generator),
        'validator_hash': digest(Path(__file__).with_name('workbook_validation.py').read_bytes()),
        'override_revision': job.override_revision, 'preview_sha256': digest(previews),
    }
    inputs = {'manifest': manifest, 'classes': source, 'components': components,
              'overrides': overrides, 'decisions': decisions, 'actual_hashes': actual_hashes,
              'completeness': extraction_completeness(job), 'status': job.status,
              'reference_metadata': {key: str(getattr(reference, key)) for key in
                  ('standard_edition', 'units', 'effective_date', 'review_status', 'frozen_at', 'schema_valid')} if reference else None}
    if any(d['discriminators_matched'].get('codelists', {}).get('profile') == 'BUHASA' for d in decisions):
        from .buhasa_codelists import dictionary
        inputs['buhasa_dictionary_hash'] = dictionary()[2]
    manifest['input_sha256'] = digest(inputs)
    return manifest, previews, errors


def persist_validation(job, result, author, trigger_source='manual', state=None):
    manifest, _, errors = state or snapshot(job)
    counts = result.get('severity_counts') or {}
    run = ValidationRun.objects.create(job=job, author=author, input_sha256=manifest['input_sha256'],
        preview_sha256=manifest['preview_sha256'], results=result, severity_counts=counts,
        error_count=int(counts.get('high', 0)) + len(errors), trigger_source=trigger_source)
    audit(job, 'validation', author, validation_id=str(run.pk), error_count=run.error_count)
    return run


def validate_release(job, author):
    from .workbook_validation import validate_extracted_workbook
    state = snapshot(job)
    result = validate_extracted_workbook(job=job, workbook_scope='both', previews=state[1])
    cat_codes = {row['cells'].get('IndustryCommodityCode') for sheet in state[1]['cat']['sheets'] for row in sheet['rows'] if row.get('source', {}).get('component_id')}
    missing = []
    for sheet in state[1]['spec']['sheets']:
        for row in sheet['rows']:
            code = row['cells'].get('CommodityCode') or row['cells'].get('IndustryCommodityCode')
            if row.get('source', {}).get('class_id') and code and code not in cat_codes:
                missing.append({'severity': 'high', 'workbook': 'spec', 'sheet_name': sheet['name'], 'row_key': row['row_key'], 'column_name': 'CommodityCode', 'message': 'SPEC commodity is absent from generated CAT.', 'value': code, 'suggestion': 'Resolve the reviewed SPEC/CAT commodity relationship.'})
    from collections import Counter
    identifiers = [row['cells'].get('IndustryCommodityCode') for sheet in state[1]['cat']['sheets'] for row in sheet['rows'] if row.get('source', {}).get('component_id') and row['cells'].get('IndustryCommodityCode')]
    for code, count in Counter(identifiers).items():
        if count > 1:
            missing.append({'severity': 'high', 'workbook': 'cat', 'sheet_name': '', 'row_key': '', 'column_name': 'IndustryCommodityCode', 'message': 'Duplicate generated commodity identifier.', 'value': code, 'suggestion': 'Resolve duplicate component identifiers before release.'})
    existing_issues = result.get('issues', [])
    total_issues = result.get('issues_total', len(existing_issues)) + len(missing)
    result['issues'] = (existing_issues + missing)[:250]
    result['truncated'] = bool(result.get('truncated')) or total_issues > 250
    result['issues_total'] = total_issues
    result.setdefault('severity_counts', {})['high'] = result.get('severity_counts', {}).get('high', 0) + len(missing)
    if missing:
        result['status'] = 'fail'
    return persist_validation(job, result, author, 'release', state)


def readiness(job, manifest, previews, errors, author=None):
    blockers = list(errors)
    blockers.extend(codelist_release_blockers(job))
    if job.status != 'completed' or extraction_completeness(job)['state'] != 'complete':
        blockers.append('Extraction must be completed with verified complete coverage.')
    reference = job.reference_workbook_set
    if not reference or not reference.frozen_at or not reference.schema_valid:
        blockers.append('A validated frozen reference set must be pinned.')
    approved = {str(value) for value in getattr(settings, 'S3D_APPROVED_REFERENCE_SET_IDS', [])}
    if not reference or str(reference.pk) not in approved:
        blockers.append('Pinned reference pack approval is unconfigured or not recorded.')
    if reference and (not reference.standard_edition or not reference.units):
        blockers.append('Reference standards edition and unit convention must be recorded.')
    authorities = {str(value) for value in getattr(settings, 'S3D_RELEASE_AUTHORITY_USER_IDS', [])}
    if not authorities:
        blockers.append('Release authority is not configured.')
    elif author and str(author.pk) not in authorities:
        blockers.append('The current user is not a configured release authority.')
    components = list(PipingClassComponent.objects.filter(piping_class__job=job))
    if not components:
        blockers.append('No extracted components are available for release.')
    cat_rows = [row for sheet in previews['cat']['sheets'] for row in sheet['rows'] if row.get('source', {}).get('component_id')]
    covered = {row['source']['component_id'] for row in cat_rows if row.get('match_decision', {}).get('approved') and (row['cells'].get('IndustryCommodityCode') or row['cells'].get('CommodityCode')) == row['match_decision'].get('commodity_code')}
    if any(str(component.pk) not in covered for component in components):
        blockers.append('Every extracted component must have an approved pinned match consumed by generated CAT rows.')
    if previews['cat'].get('unrouted_components'):
        blockers.append('Unrouted CAT components require engineering resolution.')
    run = job.validation_runs.first()
    if not run:
        blockers.append('Persist validation for both workbooks before freezing.')
    elif (run.input_sha256 != manifest['input_sha256'] or run.preview_sha256 != manifest['preview_sha256']):
        blockers.append('Validation is stale; validate the current inputs again.')
    elif run.error_count or set(run.results.get('workbooks', [])) != {'spec', 'cat'} or not run.results.get('ok'):
        blockers.append('Both workbooks must have a successful validation with zero errors.')
    elif run.results.get('truncated'):
        blockers.append('Validation was truncated; resolve reported findings and validate again.')
    return list(dict.fromkeys(blockers)), run


def canonical_workbook(data):
    """Fix ZIP metadata and Office timestamps for reproducible workbook bytes."""
    source = zipfile.ZipFile(io.BytesIO(data))
    output = io.BytesIO()
    with source, zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as target:
        for name in sorted(source.namelist()):
            content = source.read(name)
            if name == 'docProps/core.xml':
                root = ElementTree.fromstring(content)
                for kind in ('created', 'modified'):
                    node = root.find('{http://purl.org/dc/terms/}' + kind)
                    if node is not None:
                        node.text = '2000-01-01T00:00:00Z'
                content = ElementTree.tostring(root, encoding='utf-8')
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o600 << 16
            target.writestr(info, content)
    return output.getvalue()


def freeze(job, draft, author):
    from .exporters.smartplant_exporter import build_spec_workbook, build_cat_workbook
    manifest, previews, errors = snapshot(job)
    blockers, run = readiness(job, manifest, previews, errors, author)
    if draft.state != 'draft' or draft.manifest.get('input_sha256') != manifest['input_sha256']:
        blockers.append('Create a new draft from the current inputs before freezing.')
    if blockers:
        return None, blockers
    existing = draft.successors.filter(state='frozen').first()
    if existing:
        if existing.successors.filter(state='invalidated').exists():
            return None, ['The frozen release was invalidated. Create a new draft.']
        return existing, []
    spec = canonical_workbook(build_spec_workbook(job, preview_data=previews['spec'], export_state='frozen').getvalue())
    cat = canonical_workbook(build_cat_workbook(job, preview_data=previews['cat'], export_state='frozen').getvalue())
    manifest.update(spec_sha256=digest(spec), cat_sha256=digest(cat))
    release = SpecRelease.objects.create(job=job, state='frozen', parent=draft, validation=run,
        author=author, manifest=manifest, spec_bytes=spec, cat_bytes=cat)
    audit(job, 'release_frozen', author, release_id=str(release.pk), manifest=manifest)
    return release, []


def released_bytes(job, kind, release_id=None):
    problems = codelist_release_blockers(job)
    if problems:
        return None, problems, None
    qs = job.spec_releases.filter(state='frozen')
    release = qs.filter(pk=release_id).first() if release_id else qs.first()
    if not release or release.successors.filter(state='invalidated').exists():
        return None, ['A current frozen release is required. Use draft=1 for draft review.'], None
    manifest, _, errors = snapshot(job)
    if errors or manifest['input_sha256'] != release.manifest.get('input_sha256'):
        return None, errors + ['Frozen release inputs changed. Create and validate a new release.'], release
    data = bytes(getattr(release, f'{kind}_bytes'))
    if digest(data) != release.manifest.get(f'{kind}_sha256'):
        return None, ['Frozen workbook integrity check failed.'], release
    return data, [], release


def matching_changed(job, author, event):
    frozen = job.spec_releases.filter(state='frozen').first()
    if frozen:
        fresh = PaperSpecExtractionJob.objects.select_related('document', 'reference_workbook_set').get(pk=job.pk)
        manifest, _, _ = snapshot(fresh)
        if manifest['input_sha256'] != frozen.manifest.get('input_sha256'):
            invalidate(job.pk, event, author)
            return
    audit(job, event, author)


def codelist_release_blockers(job):
    from .buhasa_codelists import blockers
    problems = []
    for decision in job.match_decisions.all():
        evidence = decision.discriminators_matched.get('codelists', {})
        if evidence.get('profile') != 'BUHASA':
            continue
        problems.extend(f'{decision.pdf_component_name}: {problem}' for problem in blockers(evidence))
        if decision.state not in ('unique_match', 'ambiguous') or decision.review_decision not in ('approved', 'remapped') or not evidence.get('selected_candidate_id'):
            problems.append(f'{decision.pdf_component_name}: unresolved BUHASA catalog selection.')
    return problems
