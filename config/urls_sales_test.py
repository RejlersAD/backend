from django.urls import include, path

urlpatterns = [
    path('api/v1/sales/', include('apps.sales.urls')),
]
