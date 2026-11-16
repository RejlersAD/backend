"""
Project Management URLs
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from apps.core.project_views import (
    ProjectViewSet,
    ProjectTaskViewSet,
    ProjectMilestoneViewSet,
    SmartProjectCollectionViewSet
)
from .shared_record_views import (
    SharedRecordQueueView, SharedRecordDetailView, SharedRecordCandidatesView,
    SharedRecordLinkView, SharedRecordTargetsView,
)
from .intelligence_views import (
    IntelligenceKnowledgeView, IntelligenceKnowledgeDetailView, IntelligenceKnowledgeProcessView,
    IntelligenceConversationView, IntelligenceConversationDetailView,
    IntelligenceConversationSendView,
    IntelligenceRevisionCompareView,
)

router = DefaultRouter()
router.register(r'tasks', ProjectTaskViewSet, basename='project-task')
router.register(r'milestones', ProjectMilestoneViewSet, basename='project-milestone')
router.register(r'smart-projects', SmartProjectCollectionViewSet, basename='smart-project')
router.register(r'', ProjectViewSet, basename='project')

urlpatterns = [
    path('<int:project_id>/ai-knowledge/', IntelligenceKnowledgeView.as_view()),
    path('<int:project_id>/ai-knowledge/<int:source_id>/', IntelligenceKnowledgeDetailView.as_view()),
    path('<int:project_id>/ai-knowledge/<int:source_id>/process/', IntelligenceKnowledgeProcessView.as_view()),
    path('<int:project_id>/ai-compare/', IntelligenceRevisionCompareView.as_view()),
    path('<int:project_id>/ai-conversations/', IntelligenceConversationView.as_view()),
    path('<int:project_id>/ai-conversations/<int:conversation_id>/', IntelligenceConversationDetailView.as_view()),
    path('<int:project_id>/ai-conversations/<int:conversation_id>/send/', IntelligenceConversationSendView.as_view()),
    path('shared-records/', SharedRecordQueueView.as_view()),
    path('shared-record-targets/', SharedRecordTargetsView.as_view()),
    path('shared-records/<str:source_type>/<str:source_id>/', SharedRecordDetailView.as_view()),
    path('shared-records/<str:source_type>/<str:source_id>/candidates/', SharedRecordCandidatesView.as_view()),
    path('shared-records/<str:source_type>/<str:source_id>/link/', SharedRecordLinkView.as_view()),
    path('', include(router.urls)),
]
