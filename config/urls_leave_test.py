from django.urls import include, path
urlpatterns = [path('api/v1/payroll/', include('apps.payroll.urls'))]
