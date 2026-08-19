"""S3 snapshot synchronization for cross recommendation data."""
import json
import os
from datetime import datetime, timezone

import boto3
from django.db.models import Count, Q

from apps.pid_verification.models import PIDVDocument, PIDVFinding
from apps.pfd_quality.models import PFDQDocument, PFDQFinding
from apps.cross_recommendation.models import CrossRecommendationLink


def _pid_docs_snapshot():
    docs = PIDVDocument.objects.select_related('project', 'uploaded_by').all().order_by('-created_at')
    out = []
    for d in docs:
        agg = PIDVFinding.objects.filter(drawing__document=d).aggregate(
            total=Count('id'),
            critical=Count('id', filter=Q(severity='critical')),
            major=Count('id', filter=Q(severity='major')),
        )
        out.append({
            'document_id': str(d.document_id),
            'doc_type': 'pid',
            'file_name': d.file_name,
            'file_hash': d.file_hash,
            'status': d.status,
            'project_id': str(d.project.project_id) if d.project_id else None,
            'uploaded_by': d.uploaded_by_id,
            'created_at': d.created_at.isoformat() if d.created_at else None,
            's3_path': d.s3_path,
            'total_issues': agg['total'] or 0,
            'critical_count': agg['critical'] or 0,
            'major_count': agg['major'] or 0,
        })
    return out


def _pfd_docs_snapshot():
    docs = PFDQDocument.objects.select_related('project', 'uploaded_by').all().order_by('-created_at')
    out = []
    for d in docs:
        agg = PFDQFinding.objects.filter(drawing__document=d).aggregate(
            total=Count('id'),
            critical=Count('id', filter=Q(severity='critical')),
            major=Count('id', filter=Q(severity='major')),
        )
        out.append({
            'document_id': str(d.document_id),
            'doc_type': 'pfd',
            'file_name': d.file_name,
            'file_hash': d.file_hash,
            'status': d.status,
            'project_id': str(d.project.project_id) if d.project_id else None,
            'uploaded_by': d.uploaded_by_id,
            'created_at': d.created_at.isoformat() if d.created_at else None,
            's3_path': d.s3_path,
            'total_issues': agg['total'] or 0,
            'critical_count': agg['critical'] or 0,
            'major_count': agg['major'] or 0,
        })
    return out


def _links_snapshot():
    links = CrossRecommendationLink.objects.all().order_by('-updated_at')
    return [
        {
            'link_id': str(l.link_id),
            'source_type': l.source_type,
            'source_document_id': str(l.source_document_id),
            'target_type': l.target_type,
            'target_document_id': str(l.target_document_id),
            'project_id': str(l.project_id) if l.project_id else None,
            'score': l.score,
            'reason': l.reason,
            'decision': l.decision,
            'created_by': l.created_by_id,
            'created_at': l.created_at.isoformat() if l.created_at else None,
            'updated_at': l.updated_at.isoformat() if l.updated_at else None,
        }
        for l in links
    ]


def build_snapshot_payload():
    return {
        'snapshot_at': datetime.now(timezone.utc).isoformat(),
        'pid_documents': _pid_docs_snapshot(),
        'pfd_documents': _pfd_docs_snapshot(),
        'links': _links_snapshot(),
    }


def sync_snapshot_to_s3():
    if os.environ.get('USE_S3', 'False').lower() != 'true':
        return {'synced': False, 'reason': 'USE_S3 is disabled'}

    bucket = os.environ.get('PFD_S3_BUCKET') or os.environ.get('AWS_STORAGE_BUCKET_NAME')
    region = os.environ.get('PFD_S3_REGION') or os.environ.get('AWS_S3_REGION_NAME', 'us-east-1')
    if not bucket:
        return {'synced': False, 'reason': 'Missing bucket configuration'}

    payload = build_snapshot_payload()
    body = json.dumps(payload, ensure_ascii=True, indent=2).encode('utf-8')

    ts = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    key_latest = 'cross_recommendation/database/latest.json'
    key_version = f'cross_recommendation/database/snapshots/{ts}.json'

    s3 = boto3.client('s3', region_name=region)
    s3.put_object(Bucket=bucket, Key=key_latest, Body=body, ContentType='application/json')
    s3.put_object(Bucket=bucket, Key=key_version, Body=body, ContentType='application/json')

    return {
        'synced': True,
        'bucket': bucket,
        'latest_key': key_latest,
        'version_key': key_version,
        'pid_documents': len(payload['pid_documents']),
        'pfd_documents': len(payload['pfd_documents']),
        'links': len(payload['links']),
    }
