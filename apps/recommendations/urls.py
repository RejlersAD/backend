from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

# Create router for viewsets
router = DefaultRouter()
router.register(r'', views.RecommendationAPIViewSet, basename='recommendations')

app_name = 'recommendations'

urlpatterns = [
    # API endpoints via router
    path('', include(router.urls)),
    
    # Additional URL patterns can be added here if needed
]

# URL Patterns Reference:
# 
# Main API Endpoints:
# POST /api/v1/recommendations/analyze-upload/     - Analyze uploaded document
# POST /api/v1/recommendations/batch-analyze/      - Batch analyze multiple documents  
# GET  /api/v1/recommendations/history/            - Get recommendation history
# POST /api/v1/recommendations/feedback/           - Submit feedback on recommendations
# GET  /api/v1/recommendations/preferences/        - Get user preferences
# PUT  /api/v1/recommendations/preferences/        - Update user preferences
# GET  /api/v1/recommendations/similar-documents/  - Find similar documents
# GET  /api/v1/recommendations/statistics/         - Get recommendation statistics
#
# Example usage:
# curl -X POST http://localhost:8000/api/v1/recommendations/analyze-upload/ \
#      -H "Authorization: Bearer YOUR_JWT_TOKEN" \
#      -F "file=@document.pdf" \
#      -F "document_type=pid_drawing" \
#      -F "project_code=ADNOC-P16093"