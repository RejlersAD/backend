"""
Sales App URL Configuration
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ClientViewSet, ContactViewSet, DealViewSet, QuoteViewSet,
    SalesActivityViewSet, SalesForecastViewSet, SalesDashboardViewSet
)

# Create router and register viewsets
router = DefaultRouter()
router.register(r'clients', ClientViewSet, basename='client')
router.register(r'contacts', ContactViewSet, basename='contact')
router.register(r'deals', DealViewSet, basename='deal')
router.register(r'quotes', QuoteViewSet, basename='quote')
router.register(r'activities', SalesActivityViewSet, basename='sales-activity')
router.register(r'forecasts', SalesForecastViewSet, basename='sales-forecast')
router.register(r'dashboard', SalesDashboardViewSet, basename='sales-dashboard')

# URL patterns
urlpatterns = [
    path('', include(router.urls)),
]

# Available endpoints:
# 
# CLIENTS:
# - GET    /api/sales/clients/                     - List all clients
# - POST   /api/sales/clients/                     - Create client
# - GET    /api/sales/clients/{id}/                - Get client details
# - PUT    /api/sales/clients/{id}/                - Update client
# - DELETE /api/sales/clients/{id}/                - Delete client
# - POST   /api/sales/clients/{id}/calculate_health_score/
# - GET    /api/sales/clients/{id}/churn_prediction/
# - GET    /api/sales/clients/{id}/insights/
# - GET    /api/sales/clients/at_risk/
# - GET    /api/sales/clients/top_clients/
#
# CONTACTS:
# - GET    /api/sales/contacts/                    - List all contacts
# - POST   /api/sales/contacts/                    - Create contact
# - GET    /api/sales/contacts/{id}/               - Get contact details
# - PUT    /api/sales/contacts/{id}/               - Update contact
# - DELETE /api/sales/contacts/{id}/               - Delete contact
# - GET    /api/sales/contacts/by_client/          - Get contacts by client
#
# DEALS:
# - GET    /api/sales/deals/                       - List all deals
# - POST   /api/sales/deals/                       - Create deal
# - GET    /api/sales/deals/{id}/                  - Get deal details
# - PUT    /api/sales/deals/{id}/                  - Update deal
# - DELETE /api/sales/deals/{id}/                  - Delete deal
# - POST   /api/sales/deals/{id}/calculate_win_probability/
# - POST   /api/sales/deals/{id}/score_lead/
# - GET    /api/sales/deals/{id}/next_action/
# - GET    /api/sales/deals/pipeline_summary/
# - POST   /api/sales/deals/move_stage/
#
# QUOTES:
# - GET    /api/sales/quotes/                      - List all quotes
# - POST   /api/sales/quotes/                      - Create quote
# - GET    /api/sales/quotes/{id}/                 - Get quote details
# - PUT    /api/sales/quotes/{id}/                 - Update quote
# - DELETE /api/sales/quotes/{id}/                 - Delete quote
# - POST   /api/sales/quotes/{id}/send_to_client/
# - POST   /api/sales/quotes/{id}/mark_viewed/
#
# ACTIVITIES:
# - GET    /api/sales/activities/                  - List all activities
# - POST   /api/sales/activities/                  - Create activity
# - GET    /api/sales/activities/{id}/             - Get activity details
# - PUT    /api/sales/activities/{id}/             - Update activity
# - DELETE /api/sales/activities/{id}/             - Delete activity
# - GET    /api/sales/activities/my_activities/
# - GET    /api/sales/activities/upcoming/
#
# FORECASTS:
# - GET    /api/sales/forecasts/                   - List all forecasts
# - POST   /api/sales/forecasts/                   - Create forecast
# - GET    /api/sales/forecasts/{id}/              - Get forecast details
# - POST   /api/sales/forecasts/generate_forecast/ - Generate AI forecast
# - POST   /api/sales/forecasts/{id}/update_actual/
#
# DASHBOARD:
# - GET    /api/sales/dashboard/summary/           - Comprehensive dashboard
# - GET    /api/sales/dashboard/ai_insights/       - AI-powered insights
