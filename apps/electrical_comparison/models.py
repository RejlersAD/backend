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
