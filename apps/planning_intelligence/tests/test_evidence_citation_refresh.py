"""A citation repair survives refresh only while its exact source still applies."""
from copy import deepcopy
from datetime import date
from uuid import uuid4

from django.core.files.base import ContentFile
from django.test import TestCase

from ..evidence_models import EvidenceEdge, EvidenceNode
from ..services.evidence_graph import refresh_evidence_graph
from .test_document_evidence_api import DocumentEvidenceAPITests


class CitationRepairRefreshTests(TestCase):
    def setUp(self):
        DocumentEvidenceAPITests.setUp(self)
        self.file.file.save('requirements.csv', ContentFile(b'Original source'), save=True)
        self.graph = refresh_evidence_graph(self.project, self.user)
        self.original = self.graph.nodes.filter(kind='fact', property='identity').first()
        self.repair = EvidenceNode.objects.create(
            id=uuid4(), graph=self.graph, document_version=self.original.document_version,
            kind='fact', entity_id=self.original.entity_id, entity_name=self.original.entity_name,
            property=self.original.property, value=deepcopy(self.original.value), unit=self.original.unit,
            provenance_type='document_evidence', sources=deepcopy(self.original.sources),
            rule={'name': 'verified_source_citation_repair', 'version': '1',
                  'original_fact_id': str(self.original.pk)},
            validation=deepcopy(self.original.validation), status='accepted', current=True)
        self.edge = EvidenceEdge.objects.create(
            id=uuid4(), graph=self.graph, source=self.repair, target=self.original,
            relationship='supersedes', provenance={'rule': 'verified_source_citation_repair'})
        self.original.status = 'superseded'
        self.original.save(update_fields=['status'])

    def refresh_with_unrelated_change(self):
        self.project.planned_end_date = date(2027, 1, 31)
        self.project.save(update_fields=['planned_end_date'])
        refresh_evidence_graph(self.project, self.user)
        self.repair.refresh_from_db()
        self.edge.refresh_from_db()

    def test_unchanged_source_keeps_repaired_acceptance_on_refresh(self):
        self.refresh_with_unrelated_change()
        self.assertTrue(self.repair.current)
        self.assertTrue(self.edge.current)
        self.assertEqual(self.repair.status, 'accepted')
        self.original.refresh_from_db()
        self.assertEqual(self.original.status, 'superseded')

    def test_replaced_original_bytes_invalidate_repaired_acceptance(self):
        with self.file.file.storage.open(self.file.file.name, 'wb') as stream:
            stream.write(b'Revised source bytes')
        self.refresh_with_unrelated_change()
        self.assertFalse(self.repair.current)
        self.assertFalse(self.edge.current)

    def test_changed_value_cannot_claim_citation_repair_survival(self):
        # SQLite fixtures permit constructing a malformed repair; production
        # additionally protects existing source assertions with SQL triggers.
        EvidenceNode.objects.filter(pk=self.repair.pk).update(value='Another deliverable')
        self.refresh_with_unrelated_change()
        self.assertFalse(self.repair.current)
        self.assertFalse(self.edge.current)
