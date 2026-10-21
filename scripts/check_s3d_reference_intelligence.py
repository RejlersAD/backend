"""Apply/check only 0014 on local radai_dev and roll back synthetic API evidence.

Run from backend: python scripts/check_s3d_reference_intelligence.py [--apply]
Never targets a remote host, production database, S3 or AI provider.
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['SPEC_SKIP_CORS_ON_READY'] = '1'
os.environ['S3_AUTO_APPLY_CORS'] = '0'
os.environ['USE_S3'] = 'false'
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

import django
django.setup()

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.db import connection, transaction
from django.db.migrations.executor import MigrationExecutor
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.spec_customization import matching_views
from apps.spec_customization.matching_models import MatchingWorkbookSet, ComponentMatchDecision
from apps.spec_customization.models import PaperSpecDocument, PaperSpecExtractionJob, PipingClass, PipingClassComponent
from apps.spec_customization.project_models import SpecProject
from apps.spec_customization.services.component_matcher import parse_match_xlsx
from apps.spec_customization.services.reference_intelligence import file_sha256
from apps.spec_customization.tests_matching_pipeline import RemoteMemoryStorage, spec_xlsx, xlsx

assert connection.vendor == 'postgresql'
assert connection.settings_dict['NAME'] == 'radai_dev'
assert connection.settings_dict['HOST'] in {'postgres_local', 'radai_postgres_local', 'localhost', '127.0.0.1'}
executor = MigrationExecutor(connection)
target = [('spec_customization', '0014_reference_intelligence')]
plan = executor.migration_plan(target)
assert all(m.app_label == 'spec_customization' and m.name == '0014_reference_intelligence' and not backwards for m, backwards in plan)
if plan:
    assert '--apply' in sys.argv, '0014 pending; pass --apply to apply only this migration on radai_dev.'
    executor.migrate(target)
assert not MigrationExecutor(connection).migration_plan(target)
print('PASS: 0014 applied on local radai_dev.', flush=True)

storage = RemoteMemoryStorage()
factory = APIRequestFactory()
with transaction.atomic():
    import uuid
    owner = get_user_model().objects.create_user(username=f's3d-check-{uuid.uuid4().hex[:12]}')
    project = SpecProject.objects.create(name='Synthetic S3D rollback probe', created_by=owner)
    doc = PaperSpecDocument.objects.create(original_filename='synthetic.pdf', project_id=str(project.pk), uploaded_by=owner, sha256_hash='a' * 64)
    job = PaperSpecExtractionJob.objects.create(document=doc, created_by=owner, status='completed')
    pc = PipingClass.objects.create(job=job, class_code='A', pressure_rating='150', material_grade='A105')
    PipingClassComponent.objects.create(piping_class=pc, component_type='valve', sub_type='Gate', size_from='2')
    with patch.object(MatchingWorkbookSet._meta.get_field('match_file'), 'storage', storage), \
         patch.object(MatchingWorkbookSet._meta.get_field('spec_file'), 'storage', storage), \
         patch.object(MatchingWorkbookSet._meta.get_field('cat_file'), 'storage', storage):
        ws = MatchingWorkbookSet.objects.create(project=project, schema_valid=True, is_parsed=True,
            match_file=ContentFile(xlsx('Mappings', ['PDF', 'B', 'C', 'Catalog'], [['GATE VALVE', '', '', 'Gate Valve']]), name='Match.xlsx'),
            spec_file=ContentFile(spec_xlsx(), name='SPEC.xlsx'),
            cat_file=ContentFile(xlsx('GateValve', ['IndustryCommodityCode', 'Npd[1]', 'PressureRating[1]', 'MaterialGrade'],
                [['VLV-A', 2, 150, 'A105'], ['VLV-B', 2, 150, 'A105']]), name='CAT.xlsx'))
        assert parse_match_xlsx(ws.match_file, ws) == (1, '')
        for kind in ('match', 'spec', 'cat'):
            setattr(ws, f'{kind}_file_hash', file_sha256(getattr(ws, f'{kind}_file')))
        ws.save()

        def post(view, payload, **kwargs):
            request = factory.post('/', payload, format='json')
            force_authenticate(request, owner)
            return view(request, **kwargs)

        payload = {'job_id': str(job.pk), 'workbook_set_id': str(ws.pk)}
        response = post(matching_views.matching_run, payload)
        assert response.status_code == 200, response.data
        request = factory.get('/', {'job': str(job.pk)})
        force_authenticate(request, owner)
        listed = matching_views.matching_decisions(request)
        assert listed.status_code == 200 and listed.data['count'] == 1, listed.data
        decision = ComponentMatchDecision.objects.get(job=job)
        assert decision.state == 'ambiguous' and len(decision.candidates) == 2
        response = post(matching_views.matching_decision_review, {
            'decision_id': str(decision.pk), 'review_decision': 'approved', 'final_commodity_code': 'VLV-B'})
        assert response.status_code == 200, response.data
        assert post(matching_views.matching_run, payload).status_code == 200
        assert ComponentMatchDecision.objects.filter(job=job).count() == 1
        decision.refresh_from_db()
        assert decision.final_commodity_code == 'VLV-B'
        assert post(matching_views.matching_set_freeze, {'workbook_set_id': str(ws.pk)}).status_code == 200
        assert post(matching_views.parse_matching_workbook, {}, set_id=ws.pk).status_code == 409
        job.refresh_from_db()
        assert job.reference_workbook_set_id == ws.pk
    transaction.set_rollback(True)
print('PASS: PostgreSQL matching/pinning, ambiguity, review/rerun uniqueness and freeze guards; all synthetic rows rolled back.', flush=True)
print('Concurrency races, approved real reference pack and actual SP3D import remain separate verification gates.', flush=True)
