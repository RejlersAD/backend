from django.urls import path
from . import views

app_name = 'electrical_datasheet'

urlpatterns = [
    # Electrical datasheet extraction endpoint
    path('extract/', views.extract_electrical_datasheet, name='extract_datasheet'),
]
