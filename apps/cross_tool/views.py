"""
Cross-Tool API Views
====================
Three lightweight endpoints:

  GET  /api/v1/cross-tool/suggest/?source_type=pid   → recommendation metadata
  GET  /api/v1/cross-tool/library/?doc_type=pfd      → paginated document list
  POST /api/v1/cross-tool/sync-s3/                   → trigger manual S3 sync

SOFT-CODED: All tool metadata in TOOL_CONFIG — add new tools here only.
"""
import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from .models import CrossToolRegistry
from .serializers import CrossToolRegistrySerializer

logger = logging.getLogger(__name__)

# ── SOFT-CODED: Tool metadata registry ───────────────────────────────────────
# To add a third tool: add an entry here, create its signals, done.
TOOL_CONFIG = {
    'pid': {
        'name':           'P&ID QC',
        'full_name':      'P&ID Quality Control',
        'path':           '/engineering/process/pid-verification',
        'color':          'from-blue-600 to-indigo-600',
        'description':    'Verify tag compliance, symbol standards & drawing quality',
        'companion_type': 'pfd',
    },
    'pfd': {
        'name':           'PFD QC',
        'full_name':      'PFD Quality Control',
        'path':           '/engineering/process/pfd-quality-checker',
        'color':          'from-teal-600 to-cyan-600',
        'description':    'Check equipment tags, stream numbers & process integrity',
        'companion_type': 'pid',
    },
}


class CrossToolSuggestView(APIView):
    """
    GET /api/v1/cross-tool/suggest/?source_type=pid
    Returns recommendation data for the companion tool.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        source_type = request.query_params.get('source_type', 'pid')
        if source_type not in TOOL_CONFIG:
            return Response({'error': 'Invalid source_type. Use pid or pfd.'}, status=400)

        companion_type = TOOL_CONFIG[source_type]['companion_type']
        qs = CrossToolRegistry.objects.filter(
            uploaded_by=request.user,
            doc_type=companion_type,
        ).order_by('-registered_at')

        recent = CrossToolRegistrySerializer(qs[:5], many=True).data
        return Response({
            'source_type':       source_type,
            'suggested_type':    companion_type,
            'suggested_tool':    TOOL_CONFIG[companion_type],
            'library_count':     qs.count(),
            'recent_documents':  recent,
            'has_documents':     qs.exists(),
        })


class CrossToolLibraryView(APIView):
    """
    GET /api/v1/cross-tool/library/?doc_type=pfd&limit=20&project_id=<uuid>
    Returns paginated document list from the registry.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        doc_type   = request.query_params.get('doc_type', 'pfd')
        limit      = min(int(request.query_params.get('limit', 20)), 100)
        project_id = request.query_params.get('project_id')

        qs = CrossToolRegistry.objects.filter(
            uploaded_by=request.user,
            doc_type=doc_type,
        ).order_by('-registered_at')

        if project_id:
            qs = qs.filter(project_id=project_id)

        total = qs.count()
        return Response({
            'doc_type':  doc_type,
            'total':     total,
            'documents': CrossToolRegistrySerializer(qs[:limit], many=True).data,
            'tool_info': TOOL_CONFIG.get(doc_type, {}),
        })


class CrossToolSyncView(APIView):
    """
    POST /api/v1/cross-tool/sync-s3/
    Manually trigger an S3 manifest sync (admin / debug use).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        from .tasks import sync_registry_to_s3
        try:
            sync_registry_to_s3.delay()
            pending = CrossToolRegistry.objects.filter(s3_synced=False).count()
            return Response({'queued': True, 'pending_sync_count': pending})
        except Exception as exc:
            logger.warning('[CrossTool] Manual sync trigger failed: %s', exc)
            return Response({'error': str(exc)}, status=500)
