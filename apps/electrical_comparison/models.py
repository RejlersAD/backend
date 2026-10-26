import uuid
from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class ElectricalProject(models.Model):
    """Same shape/role as apps.pid_verification_v2.models.PIDVProject —
    a user-owned folder jobs can optionally belong to, selected on a
    project-selection screen before the main comparison page (same
    state-driven pattern as PIDVerificationV2.jsx, not a separate route)."""
    project_id = models.UUIDField(
        default=uuid.uuid4, primary_key=True)
    project_name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)


class ElectricalComparisonJob(models.Model):
    job_id = models.UUIDField(
        default=uuid.uuid4, primary_key=True)
    created_by = models.ForeignKey(
        User, on_delete=models.CASCADE)
    # Optional — SET_NULL so deleting a project never loses its jobs'
    # history, same convention as PIDVDocument.project.
    project = models.ForeignKey(
        ElectricalProject, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='jobs')
    status = models.CharField(
        max_length=20, default='uploaded')
    # uploaded / processing / completed / failed
    error_message = models.TextField(blank=True)
    pid_file_name = models.CharField(max_length=255)
    # The uploaded P&ID/SLD PDF itself, persisted permanently (unlike
    # task_context['pid_file_path'] in tasks.py, which is a throwaway
    # temp path deleted at the end of every job — see _cleanup_temp_file).
    # Added specifically so AnnotatedPDFView can re-open the original
    # drawing later to overlay comparison-result boxes on it; jobs
    # uploaded before this field existed simply have pid_file empty.
    pid_file = models.FileField(upload_to='electrical_comparison/pid_files/%Y/%m/', null=True, blank=True)
    # The uploaded Equipment List / Load List files (.xlsx or .pdf),
    # persisted for the same reason as pid_file: AnnotatedPagesView
    # re-opens them later to render the canvas viewer (highlighted rows
    # for Excel, highlighted boxes for PDF) long after the task's temp
    # copies were cleaned up.
    equipment_file = models.FileField(upload_to='electrical_comparison/equipment_lists/%Y/%m/', null=True, blank=True)
    load_list_file = models.FileField(upload_to='electrical_comparison/load_lists/%Y/%m/', null=True, blank=True)
    # Targeted Vision pass results (tag_extractor.locate_tags_on_drawing):
    # {tag: [{'page': 1-based, 'x0','y0','x1','y1' in PDF points}]} for
    # 'missing' tags the AI managed to locate on the scanned drawing
    # after all. Consumed by AnnotatedPagesView to draw [LOCATED] boxes
    # on the Drawing canvas.
    located_boxes = models.JSONField(default=dict, blank=True)
    # Per-tag comparison status at the time of the locator pass above —
    # {tag_number: status} (e.g. 'matched'/'missing'/'extra') — so
    # AnnotatedPagesView/get_annotated_pages can color a located_boxes hit
    # by its REAL status instead of always drawing it purple. Snapshotted
    # once at save time (tasks.py) rather than re-derived from
    # ElectricalComparisonResult on every read, since a tag's status is
    # fixed at job-completion time and this avoids an extra query/join on
    # every annotated-pages request.
    tag_statuses = models.JSONField(default=dict, blank=True)
    # AGPS-SP-EDS-002 drafting-standard check results
    # (services/drafting_check.run_drafting_check):
    # {'pages': [{'page', 'checks': [{'rule_id','rule','status',
    # 'evidence'}]}], 'summary': {'pass','fail','review'}}.
    drafting_results = models.JSONField(default=dict, blank=True)
    provider = models.CharField(
        max_length=20, default='claude')
    model_used = models.CharField(
        max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    # What AI Vision actually returned per page — {str(page_index): text}
    # for every page (drawing/legend/skipped alike; a skipped/legend page
    # gets a descriptive placeholder instead of real Vision output, since
    # neither one is sent to Vision — see tag_extractor.extract_electrical_
    # tags' own raw_text_per_page). Debugging-only: lets a real extraction
    # run be inspected after the fact (what did Vision see on page N?)
    # without needing working INFO-level logging to have been in place at
    # the time, since nothing else persists this anywhere.
    raw_text_per_page = models.JSONField(default=dict, blank=True)
    # Real-time progress, updated by tasks.process_electrical_comparison
    # as it runs (see that task's own docstring) — polled by
    # JobStatusView so the frontend can show a genuine progress bar
    # instead of an indefinite spinner. pages_total/pages_done cover the
    # P&ID page loop specifically (0/0 for a job with no P&ID file —
    # current_stage still progresses through parsing_excel/comparing/
    # saving_results for that case). current_stage values: 'uploading',
    # 'reading_pages', 'ai_vision', 'parsing_excel', 'comparing',
    # 'saving_results'.
    pages_total = models.IntegerField(default=0)
    pages_done = models.IntegerField(default=0)
    current_stage = models.CharField(max_length=100, blank=True)


class ElectricalComparisonResult(models.Model):
    job = models.ForeignKey(
        ElectricalComparisonJob,
        on_delete=models.CASCADE,
        related_name='results')
    tag_number = models.CharField(max_length=100)
    description = models.CharField(
        max_length=500, blank=True)
    status = models.CharField(max_length=20)
    # matched / missing / extra
    source = models.CharField(max_length=50)
    # equipment_list / load_list
    equipment_type = models.CharField(
        max_length=100, blank=True)
    # resolved from electrical legend lookup
    remarks = models.TextField(blank=True)


# ─────────────────────────────────────────────────────────────────────────
# Staged analysis flow ("RADAI SLD Comparison" canvas) — additive models;
# the legacy job/result tables above stay untouched for old analyses.
# ─────────────────────────────────────────────────────────────────────────

class SldUserAIKey(models.Model):
    """Stage 2 BYOK — one vision key per user+provider, stored ENCRYPTED
    (apps.core.ai_credentials.encrypt_api_key). The raw key is never
    echoed back to the browser, never written to logs; only key_last4
    is exposed for the 'Using your key ••••a91f' pill."""
    id = models.UUIDField(default=uuid.uuid4, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sld_ai_keys')
    provider = models.CharField(max_length=20)  # openai | claude
    encrypted_key = models.TextField()
    key_last4 = models.CharField(max_length=8)
    last_tested_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'provider'], name='uniq_sld_ai_key_per_user_provider'),
        ]


class SldAnalysisFile(models.Model):
    """Stage 1 — one uploaded file slot (sld / equipment / load) with its
    read summary (what RADAI read from the file before continuing)."""
    KIND_CHOICES = [('sld', 'SLD drawing'), ('equipment', 'Equipment List'), ('load', 'Load List')]
    id = models.UUIDField(default=uuid.uuid4, primary_key=True)
    project = models.ForeignKey(ElectricalProject, on_delete=models.CASCADE, related_name='sld_files')
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    file = models.FileField(upload_to='electrical_comparison/staged/%Y/%m/')
    original_name = models.CharField(max_length=255)
    summary = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class SldProjectSettings(models.Model):
    """Per-project staged-flow state: stage gating, active legend. Mirrors
    apps.valve_mto.ValveMTOProjectSettings' convention."""
    project = models.OneToOneField(ElectricalProject, on_delete=models.CASCADE, related_name='sld_settings')
    stage_state = models.JSONField(default=dict, blank=True)  # {'1': 'complete', ...}
    legend = models.JSONField(default=dict, blank=True)       # {source: 'drawing'|legendId, tagRules, name}
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)


class SldAnalysis(models.Model):
    """One analysis run. All tab payloads live in `result` (equipment /
    load / drafting / interfaces / cables + headline) so tabs can be
    served from a single row; evidence crops are stored as files whose
    URLs appear in the payload."""
    id = models.UUIDField(default=uuid.uuid4, primary_key=True)
    project = models.ForeignKey(ElectricalProject, on_delete=models.CASCADE, related_name='sld_analyses')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE)
    status = models.CharField(max_length=20, default='queued')  # queued/running/completed/failed
    error = models.TextField(blank=True)
    result = models.JSONField(default=dict, blank=True)
    outdated = models.BooleanField(default=False)  # a list file was replaced after this run
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
