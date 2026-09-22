"""Review decisions survive continuation only for identical verified evidence."""
from copy import deepcopy
from hashlib import sha256
from uuid import uuid4

from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone

from ..evidence_models import EvidenceNode
from ..models import DocumentIntelligenceRun, IntelligenceFact
from ..services.document_intelligence import ENGINE_VERSION
from ..services.evidence_graph import record_evidence_decision, refresh_evidence_graph
from . import test_document_evidence_api as api_fixture


class EvidenceResumeIdentityTests(TestCase):
    quote = 'Risk: Port closure may delay delivery.'
    value = {'text': 'Port closure may delay delivery.'}

    def setUp(self):
        api_fixture.DocumentEvidenceAPITests.setUp(self)
        self.file.extracted_text = f'{self.quote}\n{self.quote}\nRisk: Late inspection affects handover.'
        self.file.file.save('quoted-risks.txt', ContentFile(self.file.extracted_text.encode()), save=True)

    def analysis(self, *, value=None, line=1, key='risk:0', extra=False, method='ai'):
        run = DocumentIntelligenceRun.objects.create(project=self.project, status='succeeded',
            engine_version=ENGINE_VERSION, started_at=timezone.now(), source_file_ids=[self.file.pk])
        start = 0 if line == 1 else len(self.quote) + 1
        IntelligenceFact.objects.create(run=run, source_file=self.file, fact_type='risk', key=key,
            value=self.value if value is None else value, extraction_method=method,
            source_excerpt=self.quote, source_locator={'line': line, 'character_start': start,
                'character_end': start + len(self.quote), 'quote': self.quote,
                'extracted_text_sha256': sha256(self.file.extracted_text.encode()).hexdigest()})
        if extra:
            quote = 'Risk: Late inspection affects handover.'
            offset = self.file.extracted_text.index(quote)
            IntelligenceFact.objects.create(run=run, source_file=self.file, fact_type='risk', key='risk:additional',
                value={'text': 'Late inspection affects handover.'}, extraction_method='ai', source_excerpt=quote,
                source_locator={'line': 3, 'character_start': offset, 'character_end': offset + len(quote), 'quote': quote,
                    'extracted_text_sha256': sha256(self.file.extracted_text.encode()).hexdigest()})
        self.graph = refresh_evidence_graph(self.project, self.user)
        return run, self.graph.nodes.get(current=True, kind='fact', property='risk', entity_id=f'assertion:{self.file.pk}:{key}')

    def accept(self, fact):
        decision = record_evidence_decision(self.project, self.user, {'revision': self.graph.revision,
            'fact_id': str(fact.pk), 'action': 'accept', 'reason': 'Checked the exact source quote and its interpretation.'})
        fact.refresh_from_db()
        return decision

    def test_new_run_retains_original_fact_decision_and_detector_lineage(self):
        initial_run, original = self.analysis()
        decision = self.accept(original)
        original_sources = deepcopy(original.sources)
        resumed_run, resumed = self.analysis(extra=True)
        self.assertNotEqual(initial_run.pk, resumed_run.pk)
        self.assertEqual(resumed.pk, original.pk)
        self.assertEqual(resumed.status, 'accepted')
        self.assertEqual(resumed.sources, original_sources)
        self.assertEqual(resumed.sources[0]['extraction_run_id'], str(initial_run.pk))
        self.assertEqual(list(resumed.review_decisions.values_list('pk', flat=True)), [decision.pk])
        new_finding = self.graph.nodes.get(current=True, property='risk', entity_id=f'assertion:{self.file.pk}:risk:additional')
        self.assertEqual(new_finding.status, 'detected')
        self.assertFalse(new_finding.review_decisions.exists())

    def test_changed_value_does_not_inherit_acceptance(self):
        _, original = self.analysis()
        self.accept(original)
        _, changed = self.analysis(value={'text': 'Port closure'})
        original.refresh_from_db()
        self.assertNotEqual(changed.pk, original.pk)
        self.assertEqual(changed.status, 'detected')
        self.assertFalse(original.current)

    def test_repeated_quote_at_another_locator_does_not_inherit_acceptance(self):
        _, original = self.analysis()
        self.accept(original)
        _, changed = self.analysis(line=2)
        self.assertNotEqual(changed.pk, original.pk)
        self.assertEqual(changed.status, 'detected')
        self.assertEqual(changed.sources[0]['locator']['line'], 2)

    def test_new_original_document_bytes_invalidate_prior_review_even_when_text_is_same(self):
        _, original = self.analysis()
        self.accept(original)
        previous_doc = original.document_version_id
        self.file.file.save('quoted-risks-revised.txt', ContentFile(b'Revised original document bytes'), save=True)
        _, changed = self.analysis()
        self.assertNotEqual(changed.pk, original.pk)
        self.assertNotEqual(changed.document_version_id, previous_doc)
        self.assertEqual(changed.status, 'detected')

    def test_ambiguous_current_matches_never_pick_the_accepted_candidate(self):
        _, original = self.analysis()
        self.accept(original)
        duplicate = EvidenceNode.objects.create(id=uuid4(), graph=self.graph,
            kind=original.kind, entity_id=original.entity_id, entity_name=original.entity_name,
            property=original.property, value=original.value, sources=deepcopy(original.sources),
            document_version=original.document_version, provenance_type=original.provenance_type,
            validation={}, rule=deepcopy(original.rule), status='detected')
        _, current = self.analysis()
        self.assertNotIn(current.pk, [original.pk, duplicate.pk])
        self.assertEqual(current.status, 'detected')
        self.assertFalse(self.graph.nodes.filter(current=True, property='risk', status='accepted').exists())

    def test_unreviewed_assertions_do_not_acquire_or_copy_decisions(self):
        _, original = self.analysis()
        _, current = self.analysis()
        self.assertNotEqual(current.pk, original.pk)
        self.assertEqual(current.status, 'detected')
        self.assertFalse(self.graph.decisions.exists())

    def test_archived_acceptance_is_not_revived_if_old_value_returns(self):
        _, original = self.analysis()
        self.accept(original)
        self.analysis(value={'text': 'Port closure'})
        _, returned = self.analysis()
        self.assertNotEqual(returned.pk, original.pk)
        self.assertEqual(returned.status, 'detected')
