"""Check S3 configuration"""
from django.conf import settings

print("=" * 60)
print("AWS S3 Configuration")
print("=" * 60)
print(f"Bucket: {getattr(settings, 'AWS_STORAGE_BUCKET_NAME', 'NOT SET')}")
print(f"Region: {getattr(settings, 'AWS_S3_REGION_NAME', 'NOT SET')}")
print(f"Endpoint: {getattr(settings, 'AWS_S3_ENDPOINT_URL', 'NOT SET')}")
print(f"Signature Version: {getattr(settings, 'AWS_S3_SIGNATURE_VERSION', 'NOT SET')}")
print("=" * 60)
