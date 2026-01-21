"""
QHSE Serializers - Soft-coded serialization for API responses
Maintains compatibility with existing frontend structure
"""
from rest_framework import serializers
from .models import QHSERunningProject, QHSEAudit  # QHSESpotCheckRegister - Disabled


class QHSERunningProjectSerializer(serializers.ModelSerializer):
    """
    Running Projects Serializer - Converts to frontend-compatible format
    Maintains exact field names expected by existing frontend
    """
    # Map database fields to frontend expected field names
    srNo = serializers.IntegerField(source='sr_no')
    projectNo = serializers.CharField(source='project_no')
    projectTitle = serializers.CharField(source='project_title')
    projectTitleKey = serializers.CharField(source='project_title_key', allow_blank=True, allow_null=True, required=False)
    client = serializers.CharField()
    projectManager = serializers.CharField(source='project_manager')
    projectStartingDate = serializers.DateField(source='project_starting_date', allow_null=True, required=False)
    projectClosingDate = serializers.DateField(source='project_closing_date', allow_null=True, required=False)
    projectExtension = serializers.DateField(source='project_extension', allow_null=True, required=False)
    projectQualityEng = serializers.CharField(source='project_quality_eng')
    manHourForQuality = serializers.DecimalField(source='man_hour_for_quality', max_digits=10, decimal_places=2, required=False, default=0)
    manhoursUsed = serializers.DecimalField(source='manhours_used', max_digits=10, decimal_places=2, required=False, default=0)
    manhoursBalance = serializers.DecimalField(source='manhours_balance', max_digits=10, decimal_places=2, read_only=True)
    qualityBillabilityPercent = serializers.CharField(source='quality_billability_percent', required=False, default='0%')
    projectQualityPlanStatusRev = serializers.CharField(source='project_quality_plan_status_rev', allow_blank=True, allow_null=True, required=False)
    projectQualityPlanStatusIssueDate = serializers.DateField(source='project_quality_plan_status_issue_date', allow_null=True, required=False)
    projectAudit1 = serializers.DateField(source='project_audit_1', allow_null=True, required=False)
    projectAudit2 = serializers.DateField(source='project_audit_2', allow_null=True, required=False)
    projectAudit3 = serializers.DateField(source='project_audit_3', allow_null=True, required=False)
    projectAudit4 = serializers.DateField(source='project_audit_4', allow_null=True, required=False)
    clientAudit1 = serializers.DateField(source='client_audit_1', allow_null=True, required=False)
    clientAudit2 = serializers.DateField(source='client_audit_2', allow_null=True, required=False)
    delayInAuditsNoDays = serializers.IntegerField(source='delay_in_audits_no_days', required=False, default=0)
    carsOpen = serializers.IntegerField(source='cars_open', required=False, default=0)
    carsDelayedClosingNoDays = serializers.IntegerField(source='cars_delayed_closing_no_days', required=False, default=0)
    carsClosed = serializers.IntegerField(source='cars_closed', required=False, default=0)
    obsOpen = serializers.IntegerField(source='obs_open', required=False, default=0)
    obsDelayedClosingNoDays = serializers.IntegerField(source='obs_delayed_closing_no_days', required=False, default=0)
    obsClosed = serializers.IntegerField(source='obs_closed', required=False, default=0)
    projectKPIsAchievedPercent = serializers.CharField(source='project_kpis_achieved_percent', required=False, default='0%')
    projectCompletionPercent = serializers.CharField(source='project_completion_percent', required=False, default='0%')
    rejectionOfDeliverablesPercent = serializers.CharField(source='rejection_of_deliverables_percent', allow_blank=True, allow_null=True, required=False)
    costOfPoorQualityAED = serializers.DecimalField(source='cost_of_poor_quality_aed', max_digits=15, decimal_places=2, required=False, default=0)
    remarks = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    
    # Additional computed fields
    isOverdue = serializers.BooleanField(source='is_overdue', read_only=True)
    totalCars = serializers.IntegerField(source='total_cars', read_only=True)
    totalObs = serializers.IntegerField(source='total_obs', read_only=True)
    
    class Meta:
        model = QHSERunningProject
        fields = [
            'srNo', 'projectNo', 'projectTitle', 'projectTitleKey', 'client',
            'projectManager', 'projectStartingDate', 'projectClosingDate', 'projectExtension',
            'projectQualityEng', 'manHourForQuality', 'manhoursUsed', 'manhoursBalance',
            'qualityBillabilityPercent', 'projectQualityPlanStatusRev', 'projectQualityPlanStatusIssueDate',
            'projectAudit1', 'projectAudit2', 'projectAudit3', 'projectAudit4',
            'clientAudit1', 'clientAudit2', 'delayInAuditsNoDays',
            'carsOpen', 'carsDelayedClosingNoDays', 'carsClosed',
            'obsOpen', 'obsDelayedClosingNoDays', 'obsClosed',
            'projectKPIsAchievedPercent', 'projectCompletionPercent',
            'rejectionOfDeliverablesPercent', 'costOfPoorQualityAED', 'remarks',
            'isOverdue', 'totalCars', 'totalObs'
        ]
    
    def create(self, validated_data):
        """Auto-set created_by if user is available"""
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['created_by'] = request.user
            validated_data['updated_by'] = request.user
        return super().create(validated_data)
    
    def update(self, instance, validated_data):
        """Auto-set updated_by if user is available"""
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['updated_by'] = request.user
        return super().update(instance, validated_data)


# ============================================================================
# QHSESpotCheckRegisterSerializer - DISABLED per QHSE Manager decision
# ============================================================================
# class QHSESpotCheckRegisterSerializer(serializers.ModelSerializer):
#     """
#     Spot Check Register Serializer - Frontend-compatible format
#     """
#     srNo = serializers.IntegerField(source='sr_no')
#     projectNo = serializers.CharField(source='project_no')
#     projectTitle = serializers.CharField(source='project_title')
#     client = serializers.CharField()
#     qhseEngineer = serializers.CharField(source='qhse_engineer')
#     dateOfSpotCheck = serializers.DateField(source='date_of_spot_check')
#     time = serializers.TimeField(allow_null=True)
#     documentNo = serializers.CharField(source='document_no', allow_blank=True, allow_null=True)
#     documentTitle = serializers.CharField(source='document_title', allow_blank=True, allow_null=True)
#     originatorLead = serializers.CharField(source='originator_lead', allow_blank=True, allow_null=True)
#     comments = serializers.CharField(allow_blank=True, allow_null=True)
#     category = serializers.CharField(allow_blank=True, allow_null=True)
#     remarks = serializers.CharField(allow_blank=True, allow_null=True)
#     status = serializers.CharField()
#     resolutionDate = serializers.DateField(source='resolution_date', allow_null=True)
#     resolutionComments = serializers.CharField(source='resolution_comments', allow_blank=True, allow_null=True)
#     
#     # Computed fields
#     isOverdue = serializers.BooleanField(source='is_overdue', read_only=True)
#     
#     class Meta:
#         model = QHSESpotCheckRegister
#         fields = [
#             'srNo', 'projectNo', 'projectTitle', 'client', 'qhseEngineer',
#             'dateOfSpotCheck', 'time', 'documentNo', 'documentTitle', 'originatorLead',
#             'comments', 'category', 'remarks', 'status', 'resolutionDate', 'resolutionComments',
#             'isOverdue'
#         ]
#     
#     def create(self, validated_data):
#         """Auto-set created_by if user is available"""
#         request = self.context.get('request')
#         if request and hasattr(request, 'user'):
#             validated_data['created_by'] = request.user
#             validated_data['updated_by'] = request.user
#         return super().create(validated_data)
#     
#     def update(self, instance, validated_data):
#         """Auto-set updated_by if user is available"""
#         request = self.context.get('request')
#         if request and hasattr(request, 'user'):
#             validated_data['updated_by'] = request.user
#         return super().update(instance, validated_data)
# ============================================================================


class QHSEAuditSerializer(serializers.ModelSerializer):
    """Audit Serializer"""
    projectNo = serializers.CharField(source='project.project_no', read_only=True)
    projectTitle = serializers.CharField(source='project.project_title', read_only=True)
    auditType = serializers.CharField(source='audit_type')
    auditNumber = serializers.IntegerField(source='audit_number')
    auditDate = serializers.DateField(source='audit_date')
    auditor = serializers.CharField()
    findings = serializers.CharField(allow_blank=True, allow_null=True)
    status = serializers.CharField()
    
    class Meta:
        model = QHSEAudit
        fields = ['id', 'projectNo', 'projectTitle', 'auditType', 'auditNumber', 
                  'auditDate', 'auditor', 'findings', 'status']


class QHSEDashboardStatsSerializer(serializers.Serializer):
    """
    Dashboard Statistics Serializer - Soft-coded for flexible stats
    """
    total_projects = serializers.IntegerField()
    active_projects = serializers.IntegerField()
    overdue_projects = serializers.IntegerField()
    total_cars_open = serializers.IntegerField()
    total_cars_closed = serializers.IntegerField()
    total_obs_open = serializers.IntegerField()
    total_obs_closed = serializers.IntegerField()
    total_spot_checks = serializers.IntegerField()
    pending_spot_checks = serializers.IntegerField()
    average_quality_billability = serializers.FloatField()
    average_project_completion = serializers.FloatField()
    total_manhours_allocated = serializers.FloatField()
    total_manhours_used = serializers.FloatField()
    total_audits_completed = serializers.IntegerField()
    projects_by_client = serializers.DictField()
    monthly_spot_checks = serializers.DictField()
