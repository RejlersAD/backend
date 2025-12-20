"""
Project Management Serializers
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model
from apps.core.project_models import Project, ProjectMember, ProjectTask, ProjectMilestone

User = get_user_model()


class UserSimpleSerializer(serializers.ModelSerializer):
    """Simple user serializer for nested representations"""
    class Meta:
        model = User
        fields = ['id', 'email', 'first_name', 'last_name']


class ProjectMemberSerializer(serializers.ModelSerializer):
    """Project member serializer"""
    user = UserSimpleSerializer(read_only=True)
    user_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = ProjectMember
        fields = ['id', 'user', 'user_id', 'role', 'joined_at', 'is_active']


class ProjectTaskSerializer(serializers.ModelSerializer):
    """Project task serializer"""
    assigned_to = UserSimpleSerializer(read_only=True)
    assigned_to_id = serializers.IntegerField(write_only=True, required=False, allow_null=True)

    class Meta:
        model = ProjectTask
        fields = [
            'id', 'title', 'description', 'status', 'assigned_to', 'assigned_to_id',
            'due_date', 'priority', 'estimated_hours', 'actual_hours',
            'created_at', 'updated_at'
        ]


class ProjectMilestoneSerializer(serializers.ModelSerializer):
    """Project milestone serializer"""
    class Meta:
        model = ProjectMilestone
        fields = [
            'id', 'name', 'description', 'target_date', 'completed_date',
            'is_completed', 'created_at', 'updated_at'
        ]


class ProjectSerializer(serializers.ModelSerializer):
    """Project serializer"""
    owner = UserSimpleSerializer(read_only=True)
    owner_id = serializers.IntegerField(write_only=True, required=False)
    team_members_data = ProjectMemberSerializer(source='memberships', many=True, read_only=True)
    tasks_summary = serializers.SerializerMethodField()
    milestones_summary = serializers.SerializerMethodField()
    is_overdue = serializers.BooleanField(read_only=True)
    budget_utilization = serializers.FloatField(read_only=True)
    team_size = serializers.IntegerField(read_only=True)

    class Meta:
        model = Project
        fields = [
            'id', 'name', 'code', 'description', 'status', 'priority', 'progress',
            'start_date', 'end_date', 'owner', 'owner_id', 'team_members_data',
            'budget', 'spent', 'client_name', 'location', 'tags', 'custom_fields',
            'tasks_summary', 'milestones_summary', 'is_overdue', 'budget_utilization',
            'team_size', 'created_at', 'updated_at'
        ]

    def get_tasks_summary(self, obj):
        """Get task counts by status"""
        tasks = obj.tasks.filter(is_deleted=False)
        return {
            'total': tasks.count(),
            'todo': tasks.filter(status='todo').count(),
            'in_progress': tasks.filter(status='in_progress').count(),
            'completed': tasks.filter(status='completed').count(),
            'blocked': tasks.filter(status='blocked').count(),
        }

    def get_milestones_summary(self, obj):
        """Get milestone summary"""
        milestones = obj.milestones.filter(is_deleted=False)
        return {
            'total': milestones.count(),
            'completed': milestones.filter(is_completed=True).count(),
            'pending': milestones.filter(is_completed=False).count(),
        }


class ProjectListSerializer(serializers.ModelSerializer):
    """Lightweight project list serializer"""
    owner_name = serializers.SerializerMethodField()
    team_size = serializers.IntegerField(read_only=True)
    is_overdue = serializers.BooleanField(read_only=True)

    class Meta:
        model = Project
        fields = [
            'id', 'name', 'code', 'status', 'priority', 'progress',
            'start_date', 'end_date', 'owner_name', 'team_size',
            'is_overdue', 'created_at'
        ]

    def get_owner_name(self, obj):
        if obj.owner:
            return f"{obj.owner.first_name} {obj.owner.last_name}".strip() or obj.owner.email
        return "Unassigned"
