from django.urls import include, path

urlpatterns = [path('api/v1/enterprise/', include('apps.enterprise.urls'))]
