from django.urls import include, path

urlpatterns = [
    path('api/v1/enquiries/', include('apps.core.urls_enquiry')),
    path('api/v1/sales/', include('apps.sales.urls')),
    path('api/v1/invoice-tracker/', include('apps.invoice_tracker.urls')),
]
