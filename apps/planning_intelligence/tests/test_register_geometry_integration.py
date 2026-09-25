"""Geometry joins existing extraction without rewriting source or approvals."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.db import connection
from django.test import SimpleTestCase
from django.test.utils import CaptureQueriesContext

from ..intelligence_serializers import DocumentProfileSerializer
from ..services.document_intelligence import profile_document, run_document_intelligence
from ..services.document_plan import build_document_plan, source_files
from ..services.extraction_coverage import file_coverage, summarize_coverage
from ..services.parsers import extract_text_with_coverage
from ..services.pdf_register_geometry import extract_pdf_register_rows
from ..services.planning_package_sources import build_planning_package_sources
from ..services.register_geometry_cache import RegisterGeometrySourceChanged, cached_register_geometry, ensure_register_geometry
from ..services.register_rows import extract_register_rows, register_rows_for_file
from .test_document_intelligence import DocumentIntelligenceFixture
from .test_pdf_register_geometry import make_register_pdf


def source_pdf():
    return make_register_pdf([
        {'code': '3.1.1', 'title': 'Reliability Availability and\nMaintenance Study Report', 'merged': True},
        {'code': '3.1.2', 'title': 'Emergency Response Plan', 'remarks': 'Included in safety report'},
        {'code': '3.1.3', 'title': 'Conditional Design Report', 'remarks': 'Only if equipment changes'},
        {'code': '3.1.4', 'title': 'Excluded Design Report', 'remarks': 'Not required'},
        {'code': '3.1.5', 'title': 'Unmarked Design Report', 'marks': []},
    ])


def parsed_source():
    stream = source_pdf()
    stream.name = 'synthetic/register.pdf'
    text, _confidence, coverage = extract_text_with_coverage(stream, 'register.pdf')
    return stream, text, coverage


class RegisterGeometryReadTests(SimpleTestCase):
    def test_non_pdf_and_unsaved_sources_do_not_query_for_a_geometry_profile(self):
        from ..models import PlanningFile
        for filename, adding in [('notes.txt', False), ('register.pdf', True)]:
            with self.subTest(filename=filename):
                source = PlanningFile(pk=42, original_filename=filename, file=filename)
                source._state.adding = adding
                self.assertIsNone(cached_register_geometry(source))

    def test_parse_preserves_source_text_and_keeps_full_titles_out_of_remarks(self):
        stream, text, coverage = parsed_source()
        with patch('apps.planning_intelligence.services.register_geometry_cache.build_register_geometry', return_value={}):
            without_geometry, _, _ = extract_text_with_coverage(stream, 'register.pdf')
        self.assertEqual(text, without_geometry)
        geometry = coverage['structured_evidence']['register_geometry']
        self.assertEqual(geometry['file_sha256'], hashlib.sha256(stream.getvalue()).hexdigest())
        rows = extract_register_rows(text, structured_evidence=coverage['structured_evidence'])
        self.assertEqual(len(rows), 5)
        self.assertEqual(rows[0]['name'], 'Reliability Availability and Maintenance Study Report')
        self.assertEqual([row['applicability_status'] for row in rows],
                         ['marked', 'bundled', 'conditional', 'not_required', 'not_marked'])
        for row in rows:
            locator = row['source_locator']
            self.assertEqual(locator['quote'], text[row['start']:row['end']])
            self.assertEqual(locator['page'], 1)
            self.assertEqual(len(locator['bbox']), 4)

    def test_source_and_package_projection_preserve_review_only_inventory(self):
        _stream, text, coverage = parsed_source()
        source = {'id': 1, 'project_id': 2, 'filename': 'register.pdf', 'category': 'sow',
                  'parse_status': 'done', 'text': text, 'structured_evidence': coverage['structured_evidence']}
        plan = build_document_plan([source], project_id=2)
        self.assertEqual(len(plan['register_inventory']), 5)
        self.assertEqual([row['name'] for row in plan['activities']],
                         ['Reliability Availability and Maintenance Study Report'])
        package = build_planning_package_sources([source], [])
        self.assertEqual(len(package['deliverables']), 1)
        self.assertEqual(len(package['excluded_inventory']), 4)
        self.assertIsNone(plan['activities'][0]['duration_days'])
        self.assertFalse(plan['ready_for_calculation'])

    def test_cached_rows_replace_only_matching_page_and_item(self):
        text = 'Serial No | Discipline | Document Title\n1 | Process | First title\n\fSerial No | Discipline | Document Title\n1 | Process | Second title\n'
        fallback = extract_register_rows(text)
        replacement = deepcopy(fallback[0])
        replacement.update(name='Full first title', original_title='Full first title', source_layout='pdf_geometry_register')
        replacement['source_locator'].update(page=1, bbox=[0, 0, 10, 10])
        from ..services.register_geometry_cache import SCHEMA_VERSION
        evidence = {'register_geometry': {'schema_version': SCHEMA_VERSION, 'status': 'parsed',
                    'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'rows': [replacement]}}
        rows = extract_register_rows(text, structured_evidence=evidence)
        self.assertEqual([row['name'] for row in rows], ['Full first title', 'Second title'])
        evidence['register_geometry']['text_sha256'] = 'stale'
        self.assertEqual(extract_register_rows(text, structured_evidence=evidence), fallback)

    def test_short_ai_title_cannot_bypass_review_only_geometry_cell(self):
        _stream, text, coverage = parsed_source()
        source = {'id': 1, 'project_id': 2, 'filename': 'register.pdf', 'category': 'sow',
                  'parse_status': 'done', 'text': text, 'structured_evidence': coverage['structured_evidence']}
        quote = 'Emergency Response Plan'
        start = text.index(quote)
        fact = {'id': 9, 'source_file_id': 1, 'fact_type': 'deliverable', 'status': 'detected',
                'extraction_method': 'ai', 'value': {'name': 'Emergency Response'},
                'source_excerpt': quote, 'source_locator': {'character_start': start,
                    'character_end': start + len(quote), 'quote': quote}}
        result = build_planning_package_sources([source], [fact])
        self.assertFalse(any(row['title'] == 'Emergency Response' for row in result['deliverables']))
        self.assertTrue(any(row['reason'] == 'ai_claim_requires_register_review' for row in result['excluded_inventory']))

    def test_source_storage_text_and_size_mismatch_reject_cached_geometry(self):
        stream, text, coverage = parsed_source()
        uploaded = SimpleNamespace(file=stream, extracted_text=text, size_bytes=len(stream.getvalue()),
                                   document_profile=SimpleNamespace(extraction_coverage=coverage))
        self.assertIsNotNone(cached_register_geometry(uploaded))
        uploaded.extracted_text += 'changed'
        self.assertIsNone(cached_register_geometry(uploaded))
        uploaded.extracted_text = text
        stream.name = 'replacement/register.pdf'
        self.assertIsNone(cached_register_geometry(uploaded))
        stream.name = 'synthetic/register.pdf'
        uploaded.size_bytes += 1
        self.assertIsNone(cached_register_geometry(uploaded))

    def test_optional_geometry_library_failure_does_not_erase_extracted_text(self):
        stream, text, _coverage = parsed_source()
        from pdfminer.pdfparser import PDFSyntaxError
        with patch('apps.planning_intelligence.services.register_geometry_cache.build_register_geometry',
                   side_effect=PDFSyntaxError('Synthetic unreadable geometry')):
            fallback, _confidence, coverage = extract_text_with_coverage(stream, 'register.pdf')
        self.assertEqual(fallback, text)
        self.assertNotIn('register_geometry', coverage.get('structured_evidence') or {})


class RegisterGeometryAnalysisTests(DocumentIntelligenceFixture):
    def saved_pdf(self, *, retain_cache=False):
        stream, text, coverage = parsed_source()
        file_obj = self.source('register.pdf', 'sow', text)
        file_obj.file.save('register.pdf', ContentFile(stream.getvalue()), save=False)
        file_obj.content_type = 'application/pdf'
        file_obj.size_bytes = len(stream.getvalue())
        file_obj.save()
        geometry = coverage['structured_evidence']['register_geometry']
        geometry['source_storage_name'] = file_obj.file.name
        if not retain_cache:
            coverage.pop('structured_evidence')
        profile_document(file_obj, extraction_coverage=coverage)
        return file_obj

    def test_analysis_refreshes_once_and_resume_retains_text_revision_and_prior_facts(self):
        file_obj = self.saved_pdf()
        before = (file_obj.extracted_text, file_obj.updated_at)
        with patch('apps.planning_intelligence.services.pdf_register_geometry.extract_pdf_register_rows', wraps=extract_pdf_register_rows) as extractor:
            run, intelligence = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        self.assertEqual(extractor.call_count, 1)
        facts = list(run.facts.filter(fact_type='deliverable').order_by('pk').values('id', 'value', 'status', 'source_locator'))
        self.assertEqual(len(facts), 5)
        self.assertEqual(facts[0]['value']['name'], 'Reliability Availability and Maintenance Study Report')
        for fact in facts:
            locator = fact['source_locator']
            self.assertEqual(locator['quote'], file_obj.extracted_text[locator['character_start']:locator['character_end']])
            self.assertEqual(fact['status'], 'detected')
        self.assertEqual(intelligence['register_summary']['row_count'], 5)
        run.summary['ai_checkpoint'] = {'saved': 'unchanged checkpoint identity'}
        run.save(update_fields=['summary'])
        from ..services.document_intelligence import analyze_project
        with patch('apps.planning_intelligence.services.pdf_register_geometry.extract_pdf_register_rows', wraps=extract_pdf_register_rows) as extractor, \
                patch('apps.planning_intelligence.services.document_intelligence.analyze_project', wraps=analyze_project) as analyze:
            resumed, _ = run_document_intelligence(self.project, user=self.owner, allow_ai=False, resume_run=run)
        self.assertEqual(extractor.call_count, 0)
        self.assertEqual(analyze.call_args.kwargs['resume_state'], run.summary['ai_checkpoint'])
        self.assertNotEqual(resumed.pk, run.pk)
        self.assertEqual(facts, list(run.facts.filter(fact_type='deliverable').order_by('pk').values('id', 'value', 'status', 'source_locator')))
        file_obj.refresh_from_db()
        self.assertEqual((file_obj.extracted_text, file_obj.updated_at), before)

    def test_get_projections_do_not_read_storage_or_write_and_coverage_does_not_leak_rows(self):
        file_obj = self.saved_pdf(retain_cache=True)
        with patch('django.db.models.fields.files.FieldFile.open', side_effect=AssertionError('GET opened original storage')):
            with CaptureQueriesContext(connection) as queries:
                sources = source_files(self.project)
                rows = register_rows_for_file(file_obj)
                profile = file_obj.document_profile
                serialized = DocumentProfileSerializer(profile).data
                coverage = summarize_coverage([file_obj], analyzed_file_ids=[file_obj.pk])
        self.assertEqual(len(rows), 5)
        self.assertEqual(len(sources[0]['structured_evidence']['register_geometry']['rows']), 5)
        self.assertFalse(any(query['sql'].lstrip().upper().startswith(('UPDATE ', 'INSERT ', 'DELETE ')) for query in queries))
        for public in (serialized['extraction_coverage'], coverage['files'][0], file_coverage(file_obj, profile.extraction_coverage)):
            self.assertNotIn('structured_evidence', public)
            self.assertEqual(public['structured_evidence_summary']['register_geometry']['row_count'], 5)
        profile_document(file_obj)
        profile.refresh_from_db()
        self.assertEqual(len(profile.extraction_coverage['structured_evidence']['register_geometry']['rows']), 5)

    def test_explicit_refresh_rehashes_binary_even_when_storage_name_and_text_match(self):
        file_obj = self.saved_pdf(retain_cache=True)
        cached = cached_register_geometry(file_obj)
        altered = make_register_pdf([{'code': '3.2.1', 'title': 'Other register'}]).getvalue()
        # Deliberately simulate storage replacement without a record revision.
        # The new bytes cannot reuse the old cached geometry by name alone.
        with file_obj.file.storage.open(file_obj.file.name, 'wb') as target:
            target.write(altered)
        with patch('apps.planning_intelligence.services.pdf_register_geometry.extract_pdf_register_rows', wraps=extract_pdf_register_rows) as extractor:
            with self.assertRaises(RegisterGeometrySourceChanged):
                ensure_register_geometry(file_obj)
        self.assertEqual(extractor.call_count, 0)
        file_obj.document_profile.refresh_from_db()
        self.assertEqual(file_obj.document_profile.extraction_coverage['structured_evidence']['register_geometry'], cached)

    def test_valid_cache_refresh_changes_generation_identity_without_source_revision(self):
        file_obj = self.saved_pdf()
        run, _ = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        profile = file_obj.document_profile
        profile.refresh_from_db()
        geometry = profile.extraction_coverage['structured_evidence'].pop('register_geometry')
        profile.save(update_fields=['extraction_coverage'])
        from ..services.operational_jobs import generation_fingerprint
        request = {'generation_options': {'mode': 'planning_package', 'intelligence_run_id': run.pk}}
        before = generation_fingerprint(self.project, request)
        source_revision = file_obj.updated_at
        ensure_register_geometry(file_obj)
        after = generation_fingerprint(self.project, request)
        self.assertNotEqual(before, after)
        file_obj.refresh_from_db()
        self.assertEqual(file_obj.updated_at, source_revision)
        self.assertEqual(cached_register_geometry(file_obj)['file_sha256'], geometry['file_sha256'])

    def test_unavailable_pdf_geometry_preserves_existing_text_fallback(self):
        file_obj = self.source('absent.pdf', 'sow', 'Serial No | Discipline | Document Title\n1 | Process | Existing title\n')
        self.assertIsNone(ensure_register_geometry(file_obj))
        run, _ = run_document_intelligence(self.project, user=self.owner, allow_ai=False)
        self.assertEqual(run.facts.get(fact_type='deliverable').value['name'], 'Existing title')
