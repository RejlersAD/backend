"""
Payroll Intelligence — URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import (
    PayrollDashboardSummaryView,
    PayrollValidationLogViewSet,
    PayrollAuditAlertViewSet,
    ProjectCostAllocationViewSet,
    AIInsightSnapshotViewSet,
    ChatbotMessageViewSet,
    EmployeeLeaveRecordViewSet,
    LeaveTypeViewSet,
    LeaveRequestViewSet,
    leave_calendar,
)

app_name = 'payroll'

router = DefaultRouter()
router.register(r'validation-logs',    PayrollValidationLogViewSet,   basename='validation-log')
router.register(r'audit-alerts',       PayrollAuditAlertViewSet,      basename='audit-alert')
router.register(r'project-costs',      ProjectCostAllocationViewSet,  basename='project-cost')
router.register(r'ai-insights',        AIInsightSnapshotViewSet,      basename='ai-insight')
router.register(r'chatbot-messages',   ChatbotMessageViewSet,         basename='chatbot-message')
router.register(r'leave-records',      EmployeeLeaveRecordViewSet,    basename='leave-record')
router.register(r'leave-types',        LeaveTypeViewSet,              basename='leave-type')
router.register(r'leave-requests',     LeaveRequestViewSet,           basename='leave-request')

urlpatterns = [
    path('dashboard-summary/', PayrollDashboardSummaryView.as_view(), name='dashboard-summary'),
    path('leave-calendar/',    leave_calendar,                        name='leave-calendar'),
    path('', include(router.urls)),
]
