"""
URL Configuration for Excel Quality Checker API
Add these URLs to your main electrical_datasheet urls.py
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .excel_views import ExcelDocumentViewSet

# Create router for Excel quality checker endpoints
excel_router = DefaultRouter()
excel_router.register(r'excel-documents', ExcelDocumentViewSet, basename='excel-document')

# URL patterns to add to electrical_datasheet urls
excel_quality_checker_urls = [
    path('excel/', include(excel_router.urls)),
]

# If you want to integrate into existing urls.py, add:
#
# from .excel_urls import excel_quality_checker_urls
# 
# urlpatterns = [
#     # ... existing patterns ...
#     path('', include(excel_quality_checker_urls)),
# ]
