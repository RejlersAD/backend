"""Recommendation engine for linking P&ID and PFD documents."""
import math
import re
from difflib import SequenceMatcher
from typing import Dict, List, Optional, Tuple

from django.db.models import Count, Q
from django.utils import timezone

from apps.pid_verification.models import PIDVDocument, PIDVFinding
from apps.pfd_quality.models import PFDQDocument, PFDQFinding


def normalize_name(name: str) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', (name or '').lower()).strip()


def name_similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, normalize_name(a), normalize_name(b)).ratio()


def _issue_summary(doc_type: str, document_id) -> Dict[str, int]:
    if doc_type == 'pid':
        qs = PIDVFinding.objects.filter(drawing__document__document_id=document_id)
    else:
        qs = PFDQFinding.objects.filter(drawing__document__document_id=document_id)

    agg = qs.aggregate(
        total=Count('id'),
        critical=Count('id', filter=Q(severity='critical')),
        major=Count('id', filter=Q(severity='major')),
    )
    return {
        'total_issues': agg['total'] or 0,
        'critical_count': agg['critical'] or 0,
        'major_count': agg['major'] or 0,
    }


def _serialize_target(doc, doc_type: str, score: float, reason: str) -> Dict:
    project_id = str(doc.project.project_id) if doc.project_id else None
    summary = _issue_summary(doc_type, doc.document_id)
    return {
        'target_type': doc_type,
        'target_document_id': str(doc.document_id),
        'file_name': doc.file_name,
        'project_id': project_id,
        'status': doc.status,
        'score': round(score, 3),
        'reason': reason,
        'created_at': doc.created_at,
        **summary,
    }


def _compute_score(source_doc, target_doc) -> Tuple[float, str]:
    score = 0.0
    reasons = []

    if source_doc.project_id and target_doc.project_id and source_doc.project_id == target_doc.project_id:
        score += 0.45
        reasons.append('same project')

    sim = name_similarity(source_doc.file_name, target_doc.file_name)
    if sim >= 0.75:
        score += 0.35
        reasons.append('very similar file name')
    elif sim >= 0.45:
        score += 0.2
        reasons.append('similar file name')

    if target_doc.status == 'completed':
        score += 0.10
        reasons.append('already completed')

    diff_days = abs((source_doc.created_at - target_doc.created_at).total_seconds()) / 86400.0
    if diff_days <= 1:
        score += 0.10
        reasons.append('uploaded around same time')
    elif diff_days <= 7:
        score += 0.05
        reasons.append('uploaded within same week')

    return min(score, 0.99), ', '.join(reasons) or 'metadata match'


def _get_docs_for_type(doc_type: str, user):
    if doc_type == 'pid':
        qs = PIDVDocument.objects.select_related('project').all()
    else:
        qs = PFDQDocument.objects.select_related('project').all()

    user_obj = getattr(user, 'user', user)
    if not getattr(user_obj, 'is_staff', False):
        qs = qs.filter(uploaded_by=user)

    return qs


def _get_doc(doc_type: str, document_id, user):
    qs = _get_docs_for_type(doc_type, user)
    return qs.filter(document_id=document_id).first()


def get_recommendations(
    *,
    source_type: str,
    source_document_id: Optional[str],
    project_id: Optional[str],
    query: str,
    user,
    limit: int = 8,
) -> Dict:
    target_type = 'pfd' if source_type == 'pid' else 'pid'
    target_qs = _get_docs_for_type(target_type, user)

    if project_id:
        target_qs = target_qs.filter(project__project_id=project_id)

    if query:
        target_qs = target_qs.filter(file_name__icontains=query)

    source_doc = None
    if source_document_id:
        source_doc = _get_doc(source_type, source_document_id, user)

    if source_doc and not project_id and source_doc.project_id:
        project_id = str(source_doc.project.project_id)
        target_qs = target_qs.filter(project__project_id=project_id)

    targets = list(target_qs.order_by('-created_at')[:50])

    recs = []
    if source_doc:
        for doc in targets:
            score, reason = _compute_score(source_doc, doc)
            recs.append(_serialize_target(doc, target_type, score, reason))
        recs.sort(key=lambda r: r['score'], reverse=True)
    else:
        for doc in targets:
            recs.append(_serialize_target(doc, target_type, 0.25, 'same project / latest documents'))

    recs = recs[:limit]

    if recs:
        suggestion = {
            'action': 'search_existing',
            'message': f'Found {len(recs)} potential {target_type.upper()} match(es) in your database.',
        }
    else:
        suggestion = {
            'action': 'upload_new',
            'message': f'No {target_type.upper()} match found. You can upload a new file for quality check.',
        }

    return {
        'source_type': source_type,
        'source_document_id': str(source_document_id) if source_document_id else None,
        'project_id': project_id,
        'recommendations': recs,
        'suggestion': suggestion,
    }
