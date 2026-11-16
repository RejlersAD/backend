from django.urls import include, path

from apps.rbac.route_guard import secure_module_endpoints

urlpatterns = [path('api/v1/pid-checker-v2/', include('apps.pid_checker_v2.urls'))]
secure_module_endpoints(urlpatterns)
