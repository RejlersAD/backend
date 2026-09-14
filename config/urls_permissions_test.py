from django.urls import include, path
from apps.pid_analysis.views import PIDIssueViewSet, PIDDrawingViewSet
from apps.api.export_wrapper import pid_export_wrapper
from apps.pfd_quality.views import projects, project_detail
from apps.finance.views import InvoiceViewSet
from apps.finance.views import get_approval_details
from apps.rbac.route_guard import secure_module_endpoints
from apps.qhse.views import QHSERunningProjectViewSet
from apps.instrument_tools.views import IOListView
from apps.procurement.views import PurchaseRequisitionViewSet, PurchaseOrderViewSet

urlpatterns = [
    path('api/v1/users/', include('apps.users.urls')),
    path('', include('config.urls_rbac_test')),
    path('api/v1/sales/', include('apps.sales.urls')),
    path('api/v1/rbac/', include('apps.rbac.urls')),
    path('api/v1/procurement/requisitions/<uuid:pk>/', PurchaseRequisitionViewSet.as_view({'get': 'retrieve'})),
    path('api/v1/procurement/orders/<uuid:pk>/', PurchaseOrderViewSet.as_view({'get': 'retrieve'})),
    path('api/v1/qhse/areas/quality/projects/', QHSERunningProjectViewSet.as_view({'get': 'list'})),
    path('api/v1/qhse/areas/environmental/projects/', QHSERunningProjectViewSet.as_view({'get': 'list'})),
    path('api/v1/instrument-tools/io-list/', IOListView.as_view()),
    path('api/v1/designiq/', include('apps.designiq.urls')),
    path('api/v1/finance/invoices/', InvoiceViewSet.as_view({'get': 'list', 'post': 'create'})),
    path('api/v1/finance/invoices/<uuid:pk>/preview/', InvoiceViewSet.as_view({'get': 'preview'})),
    path('api/v1/finance/approval/<uuid:token>/details/', get_approval_details),
    path('api/v1/pid/issues/<int:pk>/', PIDIssueViewSet.as_view({'patch': 'partial_update'})),
    path('api/v1/pid/issues/<int:pk>/approve/', PIDIssueViewSet.as_view({'post': 'approve'})),
    path('api/v1/pid/drawings/', PIDDrawingViewSet.as_view({'get': 'list'})),
    path('api/v1/pid-export/<int:pk>/', pid_export_wrapper),
    path('api/v1/pfd-quality/projects/', projects),
    path('api/v1/pfd-quality/projects/<uuid:project_id>/', project_detail),
]
secure_module_endpoints(urlpatterns)
