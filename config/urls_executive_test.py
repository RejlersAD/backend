from django.urls import path
from apps.dashboard.executive_views import ExecutiveDashboardView

urlpatterns = [path('api/v1/dashboard/executive/', ExecutiveDashboardView.as_view())]
