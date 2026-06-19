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
    branch_employee_codes,
    PublicHolidayViewSet,
    AttendanceOverrideViewSet,
    SalaryComponentViewSet,
    EmployeeSalaryStructureViewSet,
    SalaryHistoryViewSet,
    annual_leave_balance,
    sync_leave_data,
    DailyWorkLogViewSet,
    generate_master_payroll,
    master_payroll_history,
    master_payroll_download,
)

app_name = 'payroll'

router = DefaultRouter()
router.register(r'validation-logs',       PayrollValidationLogViewSet,    basename='validation-log')
router.register(r'audit-alerts',          PayrollAuditAlertViewSet,       basename='audit-alert')
router.register(r'project-costs',         ProjectCostAllocationViewSet,   basename='project-cost')
router.register(r'ai-insights',           AIInsightSnapshotViewSet,       basename='ai-insight')
router.register(r'chatbot-messages',      ChatbotMessageViewSet,          basename='chatbot-message')
router.register(r'leave-records',         EmployeeLeaveRecordViewSet,     basename='leave-record')
router.register(r'leave-types',           LeaveTypeViewSet,               basename='leave-type')
router.register(r'leave-requests',        LeaveRequestViewSet,            basename='leave-request')
router.register(r'public-holidays',       PublicHolidayViewSet,              basename='public-holiday')
router.register(r'attendance-overrides',  AttendanceOverrideViewSet,         basename='attendance-override')
router.register(r'salary-components',     SalaryComponentViewSet,            basename='salary-component')
router.register(r'salary-structures',     EmployeeSalaryStructureViewSet,    basename='salary-structure')
router.register(r'salary-history',        SalaryHistoryViewSet,              basename='salary-history')
router.register(r'daily-logs',            DailyWorkLogViewSet,               basename='daily-log')

urlpatterns = [
    path('dashboard-summary/',       PayrollDashboardSummaryView.as_view(), name='dashboard-summary'),
    path('leave-calendar/',          leave_calendar,                        name='leave-calendar'),
    path('branch-employee-codes/',   branch_employee_codes,                 name='branch-employee-codes'),
    path('annual-leave-balance/',    annual_leave_balance,                  name='annual-leave-balance'),
    path('sync-leave-data/',         sync_leave_data,                       name='sync-leave-data'),
    path('generate-master-payroll/',                                   generate_master_payroll,  name='generate-master-payroll'),
    path('master-payroll-history/',                                    master_payroll_history,   name='master-payroll-history'),
    path('master-payroll-history/<uuid:import_id>/download/',          master_payroll_download,  name='master-payroll-download'),
    path('', include(router.urls)),
]
