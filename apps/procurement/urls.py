"""
Procurement Management URL Configuration
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    VendorViewSet,
    PurchaseRequisitionViewSet,
    PurchaseOrderViewSet,
    ReceiptViewSet,
    get_categories
)

router = DefaultRouter()
router.register(r'vendors', VendorViewSet, basename='vendor')
router.register(r'requisitions', PurchaseRequisitionViewSet, basename='requisition')
router.register(r'orders', PurchaseOrderViewSet, basename='order')
router.register(r'receipts', ReceiptViewSet, basename='receipt')

urlpatterns = [
    path('categories/', get_categories, name='categories'),
    path('', include(router.urls)),
]
