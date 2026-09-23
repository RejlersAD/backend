from django.urls import path
from .urls_executive_test import urlpatterns as executive_patterns
from apps.portfolio.views import PortfolioRevenueView, PortfolioWorkbookImportView, PortfolioWorkbookPreviewView, PortfolioWorkbookView
from apps.rbac.route_guard import secure_module_endpoints

urlpatterns = [
    *executive_patterns,
    path('api/v1/dashboard/executive/portfolio-workbook/', PortfolioWorkbookView.as_view()),
    path('api/v1/dashboard/executive/portfolio-workbook/revenue/', PortfolioRevenueView.as_view()),
    path('api/v1/dashboard/executive/portfolio-workbook/preview/', PortfolioWorkbookPreviewView.as_view()),
    path('api/v1/dashboard/executive/portfolio-workbook/import/', PortfolioWorkbookImportView.as_view()),
]
secure_module_endpoints(urlpatterns)
