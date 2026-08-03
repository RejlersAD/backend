"""
Subscription URL Configuration
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .subscription_views import (
    SubscriptionPlanViewSet,
    SubscriptionFeatureViewSet,
    UserSubscriptionViewSet,
    UsageTrackingViewSet,
    SubscriptionHistoryViewSet,
    SubscriptionInvoiceViewSet,
    SubscriptionDashboardViewSet,
)

# Create router
router = DefaultRouter()

# Register subscription endpoints
router.register(r'plans', SubscriptionPlanViewSet, basename='subscription-plan')
router.register(r'features', SubscriptionFeatureViewSet, basename='subscription-feature')
router.register(r'subscriptions', UserSubscriptionViewSet, basename='user-subscription')
router.register(r'usage', UsageTrackingViewSet, basename='usage-tracking')
router.register(r'history', SubscriptionHistoryViewSet, basename='subscription-history')
router.register(r'invoices', SubscriptionInvoiceViewSet, basename='subscription-invoice')
router.register(r'dashboard', SubscriptionDashboardViewSet, basename='subscription-dashboard')

urlpatterns = [
    path('', include(router.urls)),
]
