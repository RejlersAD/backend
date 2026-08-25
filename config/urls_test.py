from django.urls import include, path

urlpatterns = [
    path('api/v1/planning-intelligence/', include('apps.planning_intelligence.urls')),
    path('api/v1/project-control/', include('apps.project_control.urls')),
]
