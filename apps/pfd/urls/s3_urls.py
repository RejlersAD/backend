"""
S3 PFD/PID URL Configuration
"""

from django.urls import path
from ..views.s3_views import (
    list_s3_pfd_files,
    list_s3_pid_files,
    download_s3_file,
    upload_pfd_to_s3,
    convert_pfd_to_pid,
    get_conversion_pair,
    get_bucket_info,
    get_presigned_url,
)

urlpatterns = [
    # List files
    path('pfd-files/', list_s3_pfd_files, name='s3-list-pfd'),
    path('pid-files/', list_s3_pid_files, name='s3-list-pid'),
    
    # Download
    path('download/', download_s3_file, name='s3-download'),
    path('presigned-url/', get_presigned_url, name='s3-presigned-url'),
    
    # Upload
    path('upload-pfd/', upload_pfd_to_s3, name='s3-upload-pfd'),
    
    # Conversion
    path('convert/', convert_pfd_to_pid, name='s3-convert'),
    path('conversion-pair/', get_conversion_pair, name='s3-conversion-pair'),
    
    # Info
    path('bucket-info/', get_bucket_info, name='s3-bucket-info'),
]
