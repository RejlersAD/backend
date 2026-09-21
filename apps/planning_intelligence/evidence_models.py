"""Project-isolated evidence knowledge, independent of calculated schedules."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class EvidenceGraph(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.OneToOneField('PlanningProject', on_delete=models.CASCADE, related_name='evidence_graph')
    revision = models.PositiveIntegerField(default=0)
    schema_version = models.CharField(max_length=40)
    rule_version = models.CharField(max_length=40)
    source_fingerprint = models.CharField(max_length=64, blank=True)
    source_manifest = models.JSONField(default=list)
    built_at = models.DateTimeField(null=True)


class EvidenceDocumentVersion(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    graph = models.ForeignKey(EvidenceGraph, on_delete=models.CASCADE, related_name='documents')
    source_file = models.ForeignKey('PlanningFile', on_delete=models.PROTECT, related_name='evidence_versions')
    filename = models.CharField(max_length=512)
    storage_name = models.CharField(max_length=512)
    file_sha256 = models.CharField(max_length=64, blank=True)
    text_sha256 = models.CharField(max_length=64)
    extracted_text = models.TextField(blank=True)
    integrity_status = models.CharField(max_length=32, default='unverified')
    extraction_method = models.CharField(max_length=64, blank=True)
    extraction_run_id = models.CharField(max_length=64, blank=True)
    coverage = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class EvidenceNode(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    graph = models.ForeignKey(EvidenceGraph, on_delete=models.CASCADE, related_name='nodes')
    document_version = models.ForeignKey(EvidenceDocumentVersion, on_delete=models.PROTECT, null=True, related_name='nodes')
    kind = models.CharField(max_length=32, db_index=True)
    entity_id = models.CharField(max_length=160, db_index=True)
    entity_name = models.CharField(max_length=500, blank=True)
    property = models.CharField(max_length=64, blank=True)
    value = models.JSONField(null=True)
    unit = models.CharField(max_length=40, blank=True)
    provenance_type = models.CharField(max_length=32)
    sources = models.JSONField(default=list)
    rule = models.JSONField(default=dict)
    confidence = models.JSONField(default=dict)
    validation = models.JSONField(default=dict)
    status = models.CharField(max_length=24, default='detected', db_index=True)
    current = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['graph', 'current', 'entity_id', 'property'], name='evidence_entity_property_idx')]


class EvidenceEdge(models.Model):
    id = models.UUIDField(primary_key=True, editable=False)
    graph = models.ForeignKey(EvidenceGraph, on_delete=models.CASCADE, related_name='edges')
    source = models.ForeignKey(EvidenceNode, on_delete=models.PROTECT, related_name='outgoing_evidence_edges')
    target = models.ForeignKey(EvidenceNode, on_delete=models.PROTECT, related_name='incoming_evidence_edges')
    relationship = models.CharField(max_length=32)
    provenance = models.JSONField(default=dict)
    current = models.BooleanField(default=True)


class EvidenceDecision(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    graph = models.ForeignKey(EvidenceGraph, on_delete=models.PROTECT, related_name='decisions')
    graph_revision = models.PositiveIntegerField()
    action = models.CharField(max_length=24)
    fact = models.ForeignKey(EvidenceNode, on_delete=models.PROTECT, related_name='review_decisions')
    target_fact = models.ForeignKey(EvidenceNode, on_delete=models.PROTECT, null=True, related_name='link_decisions')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    reason = models.TextField()
    value = models.JSONField(null=True)
    unit = models.CharField(max_length=40, blank=True)
    source_fingerprint = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Evidence decisions are append-only. Record a new decision.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Evidence decisions are append-only.')
