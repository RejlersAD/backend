from django.urls import include, path

from apps.rbac.route_guard import secure_module_endpoints

urlpatterns = [
    path('api/v1/rbac/', include('apps.rbac.urls')),
    path('api/v1/users/', include('apps.users.urls')),
    path('api/v1/ai-workspaces/', include('apps.core.ai_workspace_urls')),
    path('api/v1/projects/', include('apps.core.project_urls')),
    path('api/v1/pid-checker-v2/', include('apps.pid_checker_v2.urls')),
    path('api/v1/project-control/', include('apps.project_control.urls')),
]
secure_module_endpoints(urlpatterns)
