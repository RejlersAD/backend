"""
Enquiry URL Configuration
Public endpoints for customer enquiries
"""
from django.urls import path
from .views_enquiry import submit_enquiry

urlpatterns = [
    path('submit/', submit_enquiry, name='submit-enquiry'),
]
