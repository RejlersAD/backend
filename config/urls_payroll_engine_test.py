from django.urls import include, path

from apps.rbac.route_guard import secure_module_endpoints

urlpatterns = [
    path('api/v1/payroll-engine/', include('apps.payroll_engine.urls')),
]

# Same module/action guard as config/urls.py, so tests see production access rules.
secure_module_endpoints(urlpatterns)
