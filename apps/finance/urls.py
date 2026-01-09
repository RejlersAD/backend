"""
Finance URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    InvoiceViewSet, 
    ApprovalRouteViewSet, 
    approval_action, 
    dashboard_stats,
    get_approval_details,
    submit_approval_decision
)

app_name = 'finance'

router = DefaultRouter()
router.register(r'invoices', InvoiceViewSet, basename='invoice')
router.register(r'approval-routes', ApprovalRouteViewSet, basename='approval-route')

urlpatterns = [
    # Router URLs
    path('', include(router.urls)),
    
    # Approval details and decision endpoints (no auth required - token-based)
    path('approval/<uuid:token>/details/', get_approval_details, name='approval-details'),
    path('approval/<uuid:token>/submit/', submit_approval_decision, name='approval-submit'),
    
    # Legacy approval action (email links)
    path('approve/<uuid:token>/', approval_action, name='approval-action'),
    
    # Dashboard
    path('dashboard/stats/', dashboard_stats, name='dashboard-stats'),
]
