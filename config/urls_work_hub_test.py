from django.urls import path
from apps.dashboard.work_hub import WorkHubView

urlpatterns = [path('api/v1/dashboard/work-hub/', WorkHubView.as_view(), name='work-hub')]
