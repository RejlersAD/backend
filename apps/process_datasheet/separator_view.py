from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.rbac.data_visibility_config import user_has_module_access
from .services.separator_sizing import calculate
from .services.separator_snapshots import calculator_schema


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def separator_sizing_view(request):
    if not request.user.is_superuser and not user_has_module_access(request.user, "process_datasheet"):
        return Response({"error": "Process Datasheet module access is required."}, status=403)
    if request.method == "GET":
        return Response(calculator_schema())
    try:
        result = calculate(request.data)
    except (ValueError, OverflowError, ZeroDivisionError) as exc:
        return Response({"error": str(exc) or "Inputs exceed supported numerical bounds."}, status=400)
    return Response(result)
