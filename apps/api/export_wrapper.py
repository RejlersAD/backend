from rest_framework.decorators import api_view, permission_classes
from rest_framework import permissions
from rest_framework.response import Response
import traceback

# Importing models/service from pid_analysis
from apps.pid_analysis.models import PIDDrawing
from apps.pid_analysis.export_service import PIDReportExportService


@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def pid_export_wrapper(request, pk):
    """Lightweight wrapper that calls PIDReportExportService for exports.
    This avoids touching existing core export logic and provides a stable URL.
    """
    print(f"[WRAPPER EXPORT] Called PK={pk}")
    try:
        drawing = PIDDrawing.objects.get(id=pk)
    except PIDDrawing.DoesNotExist:
        return Response({'error': 'Drawing not found'}, status=404)

    if not hasattr(drawing, 'analysis_report'):
        return Response({'error': 'No analysis report available'}, status=404)

    export_format = request.query_params.get('format', 'pdf')
    svc = PIDReportExportService()

    try:
        if export_format == 'pdf':
            return svc.export_pdf(drawing)
        elif export_format == 'excel':
            return svc.export_excel(drawing)
        elif export_format == 'csv':
            return svc.export_csv(drawing)
        else:
            return Response({'error': 'Invalid format'}, status=400)
    except Exception as e:
        traceback.print_exc()
        return Response({'error': str(e)}, status=500)
