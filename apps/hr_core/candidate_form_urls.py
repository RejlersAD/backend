from django.urls import path
from .recruitment_forms import CandidateFormAccessView, CandidateFormSessionView, CandidateFormTemplateView
from .recruitment_public_links import PublicApplicationAccessView
from .recruitment_cv import CandidateUploadView, CandidateAttachmentView, CandidateCVExtractView

urlpatterns = [
    path('uploads/', CandidateUploadView.as_view()),
    path('uploads/<int:pk>/', CandidateAttachmentView.as_view()),
    path('extract-cv/', CandidateCVExtractView.as_view()),
    path('templates/<str:kind>/', CandidateFormTemplateView.as_view()),
    path('apply/', PublicApplicationAccessView.as_view()),
    path('access/', CandidateFormAccessView.as_view()),
    path('session/', CandidateFormSessionView.as_view()),
]
