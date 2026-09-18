from django.urls import path
from apps.dashboard.work_hub import WorkHubView
from apps.dashboard.work_hub_tasks import WorkHubTaskView

urlpatterns = [
    path('api/v1/dashboard/work-hub/', WorkHubView.as_view(), name='work-hub'),
    path('api/v1/dashboard/work-hub/tasks/<int:task_id>/', WorkHubTaskView.as_view(), name='work-hub-task'),
]
