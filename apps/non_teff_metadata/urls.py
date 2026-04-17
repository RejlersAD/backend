from django.urls import path
from . import views

urlpatterns = [
    path('upload/', views.upload_non_teff_file, name='non-teff-upload'),
    path('status/<str:job_id>/', views.get_non_teff_status, name='non-teff-status'),
    path('results/<str:job_id>/', views.get_non_teff_results, name='non-teff-results'),
    path('export/<str:job_id>/', views.export_non_teff_excel, name='non-teff-export'),
]
