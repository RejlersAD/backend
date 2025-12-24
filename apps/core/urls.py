"""
Core App URL Configuration
Includes S3 storage API endpoints
"""
from django.urls import path
from .s3_views import (
    S3HealthCheckView,
    S3FileListView,
    S3FileUploadView,
    S3FileDownloadView,
    S3FileDeleteView,
    S3PresignedUploadView,
    S3FileInfoView,
    S3FileCopyView,
    S3FolderListView,
)

app_name = 'core'

urlpatterns = [
    # S3 Storage Endpoints
    path('s3/health/', S3HealthCheckView.as_view(), name='s3-health'),
    path('s3/folders/', S3FolderListView.as_view(), name='s3-folders'),
    path('s3/files/', S3FileListView.as_view(), name='s3-list'),
    path('s3/upload/', S3FileUploadView.as_view(), name='s3-upload'),
    path('s3/download/', S3FileDownloadView.as_view(), name='s3-download'),
    path('s3/delete/', S3FileDeleteView.as_view(), name='s3-delete'),
    path('s3/presigned-upload/', S3PresignedUploadView.as_view(), name='s3-presigned-upload'),
    path('s3/info/', S3FileInfoView.as_view(), name='s3-info'),
    path('s3/copy/', S3FileCopyView.as_view(), name='s3-copy'),
]
