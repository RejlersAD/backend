from django.urls import path

from . import views

app_name = 'cross_recommendation'

urlpatterns = [
    path('recommendations/', views.recommendations, name='recommendations'),
    path('links/', views.create_or_update_link, name='links'),
    path('snapshot/sync/', views.sync_snapshot, name='snapshot-sync'),
]
