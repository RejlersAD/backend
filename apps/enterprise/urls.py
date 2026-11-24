from django.urls import path

from . import views

app_name = 'enterprise'

urlpatterns = [
    path('context/', views.ContextView.as_view(), name='context'),
    path('teams/', views.TeamListView.as_view(), name='teams'),
    path('teams/<uuid:team_id>/', views.TeamDetailView.as_view(), name='team-detail'),
    path('teams/<uuid:team_id>/members/', views.TeamMembersView.as_view(), name='team-members'),
    path('teams/<uuid:team_id>/members/<int:user_id>/', views.TeamMemberDetailView.as_view(), name='team-member'),
    path('teams/<uuid:team_id>/workspaces/', views.TeamWorkspacesView.as_view(), name='team-workspaces'),
    path('teams/<uuid:team_id>/workspaces/<int:workspace_id>/', views.TeamWorkspaceDetailView.as_view(),
         name='team-workspace'),
    path('my-workspaces/', views.MyWorkspacesView.as_view(), name='my-workspaces'),
    path('organization/edition/', views.EditionView.as_view(), name='edition'),
    path('organization/policy/', views.PolicyView.as_view(), name='policy'),
    path('organization/retention-preview/', views.RetentionPreviewView.as_view(), name='retention-preview'),
    path('organization/admins/', views.OrgAdminsView.as_view(), name='org-admins'),
    path('organization/admins/<int:admin_id>/', views.OrgAdminDetailView.as_view(), name='org-admin'),
    path('audit-logs/', views.AuditLogView.as_view(), name='audit-logs'),
    path('audit-logs/export/', views.AuditLogExportView.as_view(), name='audit-logs-export'),
    path('audit-logs/summary/', views.AuditSummaryView.as_view(), name='audit-logs-summary'),
    path('organization/models/', views.OrgModelsView.as_view(), name='org-models'),
]
