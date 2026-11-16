"""AI project uploads live outside the publicly served MEDIA_ROOT."""
from pathlib import Path

from django.conf import settings
from django.utils.deconstruct import deconstructible


if getattr(settings, 'USE_S3', False):
    from storages.backends.s3boto3 import S3Boto3Storage

    @deconstructible
    class PrivateAIWorkspaceStorage(S3Boto3Storage):
        location = 'media/private_ai_workspaces'
        default_acl = 'private'
        file_overwrite = False
        custom_domain = False

        def url(self, name):
            raise ValueError('AI workspace files have no public URL.')
else:
    from django.core.files.storage import FileSystemStorage

    @deconstructible
    class PrivateAIWorkspaceStorage(FileSystemStorage):
        def __init__(self):
            root = getattr(settings, 'AI_WORKSPACE_STORAGE_ROOT', Path(settings.BASE_DIR).parent / 'private_ai_workspaces')
            super().__init__(location=root, base_url=None)

        def url(self, name):
            raise ValueError('AI workspace files have no public URL.')
