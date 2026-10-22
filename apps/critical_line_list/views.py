"""
Critical Line List — project + workspace-data API.

Projects are owner-scoped and soft-deleted. The records payload lives
either in the database (CllProjectData) or in S3 at
``cll/<owner_id>/<project_id>/workspace.json`` depending on the project's
``storage`` choice. S3 access mirrors apps.electrical_checklist.s3_service
(region-pinned endpoint, env credentials); if S3 is not configured or
unreachable the data endpoints return 503 with
``{detail: 'S3 storage is not configured'}`` so the frontend can fall back
to local caching.
"""
import json
import logging
import os

import boto3
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import CllProject, CllProjectData
from .serializers import CllProjectSerializer

logger = logging.getLogger(__name__)

S3_NOT_CONFIGURED = {'detail': 'S3 storage is not configured'}


def _s3_client():
    bucket = os.environ.get('AWS_STORAGE_BUCKET_NAME')
    if not bucket:
        return None, None
    region = os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')
    client = boto3.client(
        's3',
        region_name=region,
        endpoint_url=f'https://s3.{region}.amazonaws.com',
        aws_access_key_id=os.environ.get('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.environ.get('AWS_SECRET_ACCESS_KEY'),
    )
    return client, bucket


def _s3_key(project):
    return f'cll/{project.owner_id}/{project.id}/workspace.json'


class CllProjectViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = CllProjectSerializer
    http_method_names = ['get', 'post', 'put', 'patch', 'delete', 'head', 'options']

    def get_queryset(self):
        return CllProject.objects.filter(owner=self.request.user, is_deleted=False)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    def perform_destroy(self, instance):
        instance.is_deleted = True
        instance.save(update_fields=['is_deleted', 'updated_at'])

    @action(detail=True, methods=['get', 'put'], url_path='data')
    def data(self, request, pk=None):
        project = self.get_object()
        if project.storage == CllProject.STORAGE_S3:
            return self._data_s3(request, project)
        return self._data_db(request, project)

    @action(detail=True, methods=['get', 'put'], url_path='criteria')
    def criteria(self, request, pk=None):
        """GET/PUT the project's stress-criticality CriteriaTable (stored as
        the ``criteria`` key of the CllProjectData payload)."""
        project = self.get_object()
        if project.storage == CllProject.STORAGE_S3:
            return self._criteria_s3(request, project)
        data = CllProjectData.objects.filter(project=project).first()
        if request.method == 'GET':
            return Response({'criteria': (data.payload or {}).get('criteria') if data else None})
        payload = data.payload if data and isinstance(data.payload, dict) else {}
        payload['criteria'] = request.data.get('criteria')
        CllProjectData.objects.update_or_create(project=project, defaults={'payload': payload})
        return Response({'ok': True})

    def _criteria_s3(self, request, project):
        try:
            client, bucket = _s3_client()
        except Exception as exc:
            logger.warning('[CLL] S3 client init failed: %s', exc)
            return Response(S3_NOT_CONFIGURED, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if client is None:
            return Response(S3_NOT_CONFIGURED, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        key = _s3_key(project)
        try:
            body = {}
            try:
                obj = client.get_object(Bucket=bucket, Key=key)
                body = json.loads(obj['Body'].read().decode('utf-8'))
            except client.exceptions.NoSuchKey:
                body = {}
            if request.method == 'GET':
                return Response({'criteria': body.get('criteria') if isinstance(body, dict) else None})
            if not isinstance(body, dict):
                body = {}
            body['criteria'] = request.data.get('criteria')
            client.put_object(Bucket=bucket, Key=key,
                              Body=json.dumps(body).encode('utf-8'),
                              ContentType='application/json')
            return Response({'ok': True})
        except Exception as exc:
            logger.warning('[CLL] S3 criteria failed for project %s: %s', project.id, exc)
            return Response(S3_NOT_CONFIGURED, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    def _data_db(self, request, project):
        if request.method == 'GET':
            data = CllProjectData.objects.filter(project=project).first()
            if not data:
                return Response({'payload': None, 'updatedAt': None})
            return Response({'payload': data.payload, 'updatedAt': data.updated_at})
        payload = request.data
        data, _ = CllProjectData.objects.update_or_create(
            project=project, defaults={'payload': payload},
        )
        return Response({'updatedAt': data.updated_at})

    def _data_s3(self, request, project):
        try:
            client, bucket = _s3_client()
        except Exception as exc:
            logger.warning('[CLL] S3 client init failed: %s', exc)
            return Response(S3_NOT_CONFIGURED, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if client is None:
            return Response(S3_NOT_CONFIGURED, status=status.HTTP_503_SERVICE_UNAVAILABLE)
        key = _s3_key(project)
        try:
            if request.method == 'GET':
                try:
                    obj = client.get_object(Bucket=bucket, Key=key)
                except client.exceptions.NoSuchKey:
                    return Response({'payload': None, 'updatedAt': None})
                body = json.loads(obj['Body'].read().decode('utf-8'))
                return Response({
                    'payload': body,
                    'updatedAt': obj['LastModified'].isoformat() if obj.get('LastModified') else None,
                })
            client.put_object(
                Bucket=bucket,
                Key=key,
                Body=json.dumps(request.data).encode('utf-8'),
                ContentType='application/json',
            )
            head = client.head_object(Bucket=bucket, Key=key)
            return Response({
                'updatedAt': head['LastModified'].isoformat() if head.get('LastModified') else None,
            })
        except Exception as exc:
            logger.warning('[CLL] S3 operation failed for project %s: %s', project.id, exc)
            return Response(S3_NOT_CONFIGURED, status=status.HTTP_503_SERVICE_UNAVAILABLE)
