"""
Project Management Models
"""
from django.db import models
from django.conf import settings
from apps.core.models import BaseModel


class Project(BaseModel):
    """
    Project model for managing engineering projects
    """
    STATUS_CHOICES = [
        ('planning', 'Planning'),
        ('active', 'Active'),
        ('on_hold', 'On Hold'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    PRIORITY_CHOICES = [
        ('low', 'Low'),
        ('medium', 'Medium'),
        ('high', 'High'),
        ('critical', 'Critical'),
    ]

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, unique=True, help_text="Unique project code")
    description = models.TextField(blank=True)
    
    # Project Details
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='planning')
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='medium')
    progress = models.IntegerField(default=0, help_text="Project completion percentage (0-100)")
    
    # Dates
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    
    # People
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name='owned_projects'
    )
    team_members = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through='ProjectMember',
        related_name='projects'
    )
    
    # Budget
    budget = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    spent = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    
    # Metadata
    client_name = models.CharField(max_length=255, blank=True)
    location = models.CharField(max_length=255, blank=True)
    tags = models.JSONField(default=list, blank=True)
    custom_fields = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Project'
        verbose_name_plural = 'Projects'
        indexes = [
            models.Index(fields=['status', '-created_at']),
            models.Index(fields=['owner', 'status']),
        ]

    def __str__(self):
        return f"{self.code} - {self.name}"

    @property
    def is_overdue(self):
        """Check if project is past end date"""
        if self.end_date and self.status not in ['completed', 'cancelled']:
            from django.utils import timezone
            return timezone.now().date() > self.end_date
        return False

    @property
    def budget_utilization(self):
        """Calculate budget utilization percentage"""
        if self.budget and self.budget > 0:
            return (float(self.spent) / float(self.budget)) * 100
        return 0

    @property
    def team_size(self):
        """Get number of team members"""
        return self.team_members.count()


class ProjectMember(models.Model):
    """
    Project team member with role
    """
    ROLE_CHOICES = [
        ('project_manager', 'Project Manager'),
        ('lead_engineer', 'Lead Engineer'),
        ('engineer', 'Engineer'),
        ('designer', 'Designer'),
        ('qa', 'QA/QC'),
        ('reviewer', 'Reviewer'),
        ('viewer', 'Viewer'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='memberships')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    role = models.CharField(max_length=50, choices=ROLE_CHOICES, default='engineer')
    joined_at = models.DateTimeField(auto_now_add=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ['project', 'user']
        ordering = ['joined_at']

    def __str__(self):
        return f"{self.user.email} - {self.project.code} ({self.role})"


class ProjectTask(BaseModel):
    """
    Tasks within a project
    """
    STATUS_CHOICES = [
        ('todo', 'To Do'),
        ('in_progress', 'In Progress'),
        ('review', 'In Review'),
        ('completed', 'Completed'),
        ('blocked', 'Blocked'),
    ]

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='tasks')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='todo')
    
    assigned_to = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='assigned_tasks'
    )
    
    due_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=20, choices=Project.PRIORITY_CHOICES, default='medium')
    estimated_hours = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    actual_hours = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    
    class Meta:
        ordering = ['due_date', '-created_at']

    def __str__(self):
        return f"{self.project.code} - {self.title}"


class ProjectMilestone(BaseModel):
    """
    Project milestones
    """
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='milestones')
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    target_date = models.DateField()
    completed_date = models.DateField(null=True, blank=True)
    is_completed = models.BooleanField(default=False)
    
    class Meta:
        ordering = ['target_date']

    def __str__(self):
        return f"{self.project.code} - {self.name}"
