"""
Finance URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import InvoiceViewSet, ApprovalRouteViewSet, approval_action, dashboard_stats

app_name = 'finance'

router = DefaultRouter()
router.register(r'invoices', InvoiceViewSet, basename='invoice')
router.register(r'approval-routes', ApprovalRouteViewSet, basename='approval-route')

urlpatterns = [
    # Router URLs
    path('', include(router.urls)),
    
    # Approval actions (email links)
    path('approve/<uuid:token>/', approval_action, name='approval-action'),
    
    # Dashboard
    path('dashboard/stats/', dashboard_stats, name='dashboard-stats'),
]
