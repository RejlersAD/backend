from django.urls import include, path

urlpatterns = [
    path('api/v1/file-replica/', include('apps.file_replica.urls')),
]
