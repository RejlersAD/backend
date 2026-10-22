from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import CllProjectViewSet

app_name = 'critical_line_list'

router = DefaultRouter()
router.register('projects', CllProjectViewSet, basename='cll-project')

urlpatterns = [
    path('', include(router.urls)),
]
