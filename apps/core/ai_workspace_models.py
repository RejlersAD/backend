"""Independent, member-scoped RADAI Intelligence workspaces."""
from django.conf import settings
from django.db import models
from django.db.models import Q
from .ai_workspace_storage import PrivateAIWorkspaceStorage


class AIWorkspace(models.Model):
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='owned_ai_workspaces')
    visibility = models.CharField(max_length=12, choices=[('private', 'Private'), ('shared', 'Shared')], default='private')
    folder = models.CharField(max_length=120, blank=True)
    enterprise_project = models.ForeignKey('core.Project', null=True, blank=True, on_delete=models.SET_NULL)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'core_ai_workspaces'


class AIWorkspaceMember(models.Model):
    workspace = models.ForeignKey(AIWorkspace, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=12, choices=[('editor', 'Editor'), ('viewer', 'Viewer')])
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'core_ai_workspace_members'
        constraints = [models.UniqueConstraint(fields=['workspace', 'user'], name='core_ai_workspace_member_unique')]


class AIWorkspaceInstructions(models.Model):
    workspace = models.OneToOneField(AIWorkspace, on_delete=models.CASCADE, related_name='instructions')
    role_behavior = models.TextField(blank=True)
    goals = models.TextField(blank=True)
    response_format = models.TextField(blank=True)
    terminology = models.TextField(blank=True)
    standards = models.TextField(blank=True)
    evidence_requirements = models.TextField(blank=True)
    constraints = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'core_ai_workspace_instructions'


class AIWorkspaceDocument(models.Model):
    workspace = models.ForeignKey(AIWorkspace, on_delete=models.CASCADE, related_name='documents')
    file = models.FileField(upload_to='%Y/%m/', storage=PrivateAIWorkspaceStorage())
    name = models.CharField(max_length=255)
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, default='pending', choices=[
        ('pending', 'Pending'), ('processing', 'Processing'), ('ready', 'Ready'),
        ('failed', 'Failed'), ('needs_review', 'Needs review')])
    extracted_pages = models.JSONField(default=list, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    error = models.CharField(max_length=300, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'core_ai_workspace_documents'
        constraints = [models.UniqueConstraint(fields=['workspace', 'name', 'version'], name='core_ai_doc_version_unique')]


class AIWorkspaceConversation(models.Model):
    workspace = models.ForeignKey(AIWorkspace, null=True, blank=True, on_delete=models.CASCADE,
                                  related_name='conversations')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    title = models.CharField(max_length=160, default='New conversation')
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'core_ai_workspace_conversations'
        indexes = [models.Index(fields=['workspace', 'owner', '-updated_at'])]


class AIWorkspaceConversationShare(models.Model):
    conversation = models.ForeignKey(AIWorkspaceConversation, on_delete=models.CASCADE, related_name='shares')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'core_ai_workspace_conversation_shares'
        constraints = [models.UniqueConstraint(fields=['conversation', 'user'], name='core_ai_conversation_share_unique')]


class AIWorkspaceAttachment(models.Model):
    conversation = models.ForeignKey(AIWorkspaceConversation, null=True, blank=True,
                                     on_delete=models.CASCADE, related_name='attachments')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    file = models.FileField(upload_to='%Y/%m/', storage=PrivateAIWorkspaceStorage())
    name = models.CharField(max_length=255)
    size = models.PositiveIntegerField()
    content_type = models.CharField(max_length=100, blank=True)
    sha256 = models.CharField(max_length=64, blank=True)
    extracted_pages = models.JSONField(default=list, blank=True)
    extraction_truncated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'core_ai_workspace_attachments'
        indexes = [models.Index(fields=['conversation', '-created_at'])]


class AIWorkspaceGeneratedDocument(models.Model):
    conversation = models.ForeignKey(AIWorkspaceConversation, null=True, blank=True,
                                     on_delete=models.CASCADE, related_name='generated_documents')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    file = models.FileField(upload_to='%Y/%m/', storage=PrivateAIWorkspaceStorage())
    name = models.CharField(max_length=255)
    format = models.CharField(max_length=10)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=20, default='generated', choices=[
        ('generated', 'Generated'), ('pending_review', 'Pending review'),
        ('approved', 'Approved'), ('rejected', 'Rejected')])
    review_notes = models.TextField(blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                    on_delete=models.SET_NULL, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'core_ai_workspace_generated_documents'
        indexes = [models.Index(fields=['conversation', '-created_at'])]


class AIWorkspaceMessage(models.Model):
    conversation = models.ForeignKey(AIWorkspaceConversation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=12, choices=[('user', 'User'), ('assistant', 'Assistant')])
    content = models.TextField()
    provider = models.CharField(max_length=30, blank=True)
    model = models.CharField(max_length=100, blank=True)
    sources = models.JSONField(default=list, blank=True)
    attachments = models.JSONField(default=list, blank=True)
    token_usage = models.JSONField(default=dict, blank=True)
    instruction_version = models.PositiveIntegerField(default=0)
    request_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'core_ai_workspace_messages'
        constraints = [models.UniqueConstraint(fields=['conversation', 'request_id', 'role'],
                                               condition=Q(request_id__isnull=False), name='core_ai_message_request_role_unique')]


class AICustomSkill(models.Model):
    """A skill a user wrote for RADAI Intelligence: private, or shared read-only with their organization."""
    VISIBILITY = [('private', 'Private'), ('organization', 'Organization')]
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='ai_custom_skills')
    organization = models.ForeignKey('rbac.Organization', null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name='ai_custom_skills')
    name = models.CharField(max_length=80)
    category = models.CharField(max_length=40, default='Custom')
    description = models.CharField(max_length=300, blank=True)
    needs = models.JSONField(default=list, blank=True)
    output = models.CharField(max_length=200, blank=True)
    instructions = models.JSONField(default=list)
    starter = models.TextField(max_length=1000, blank=True)
    visibility = models.CharField(max_length=16, choices=VISIBILITY, default='private')
    based_on = models.CharField(max_length=60, blank=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'core_ai_custom_skills'
        indexes = [models.Index(fields=['owner', 'archived_at']), models.Index(fields=['organization', 'visibility'])]
