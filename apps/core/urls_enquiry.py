"""
Enquiry URL Configuration
Public endpoints for customer enquiries + admin management endpoints.
"""
from django.urls import path
from .views_enquiry import (
    submit_enquiry,
    list_enquiries,
    enquiry_detail,
    enquiry_stats,
    enquiry_response,
    enquiry_options,
    enquiry_representatives,
    my_enquiries,
    my_enquiry_detail,
    my_enquiry_response,
    my_enquiry_resolution,
    my_enquiry_feedback,
    public_enquiry_feedback,
    enquiry_escalate,
    enquiry_propose_resolution,
)

urlpatterns = [
    # Public
    path('submit/', submit_enquiry, name='submit-enquiry'),
    path('options/', enquiry_options, name='enquiry-options'),
    path('mine/', my_enquiries, name='my-enquiries'),
    path('mine/<int:pk>/', my_enquiry_detail, name='my-enquiry-detail'),
    path('mine/<int:pk>/respond/', my_enquiry_response, name='my-enquiry-response'),
    path('mine/<int:pk>/resolution/', my_enquiry_resolution, name='my-enquiry-resolution'),
    path('mine/<int:pk>/feedback/', my_enquiry_feedback, name='my-enquiry-feedback'),
    path('feedback/<uuid:token>/', public_enquiry_feedback, name='public-enquiry-feedback'),

    # Admin (HasModuleAccess: enquiry_management)
    path('',             list_enquiries, name='list-enquiries'),
    path('stats/',       enquiry_stats,  name='enquiry-stats'),
    path('representatives/', enquiry_representatives, name='enquiry-representatives'),
    path('<int:pk>/respond/', enquiry_response, name='enquiry-response'),
    path('<int:pk>/escalate/', enquiry_escalate, name='enquiry-escalate'),
    path('<int:pk>/resolve/', enquiry_propose_resolution, name='enquiry-propose-resolution'),
    path('<int:pk>/',    enquiry_detail, name='enquiry-detail'),
]
