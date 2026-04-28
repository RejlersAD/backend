"""
Pump Hydraulic Snapshot — Serializer + ViewSet
==============================================
Tiny, focused module so the existing serializers.py / views.py stay clean.
Wired into urls.py via DefaultRouter().

Soft-coded:
  • Project-key fallback chain matches the frontend mapper.
  • Surfaced metadata field list lives in `META_FIELD_KEYS` — adding a
    new field is a one-line change.
  • Per-user retention cap is configurable via env (`PUMP_HYDRAULIC_HISTORY_MAX`).
"""
from decouple import config as env_config
from django.db.models import Count, Max
from rest_framework import serializers, viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import PumpHydraulicSnapshot

# ─── Soft-coded constants ────────────────────────────────────────────────
PROJECT_KEY_FALLBACK = ("job_no", "contract_no", "project_title", "client_job_no")
META_FIELD_KEYS = ("project_title", "job_no", "client_name", "pump_tag_no", "calculation_no")
MAX_SNAPSHOTS_PER_USER = int(env_config("PUMP_HYDRAULIC_HISTORY_MAX", default=500, cast=int))


def _derive_project_key(form_state: dict) -> str:
    for key in PROJECT_KEY_FALLBACK:
        v = form_state.get(key) if isinstance(form_state, dict) else None
        if v not in (None, "") and str(v).strip():
            return str(v).strip()[:255]
    return "Untitled Project"


def _extract_meta(form_state: dict) -> dict:
    fs = form_state if isinstance(form_state, dict) else {}
    return {k: str(fs.get(k, "") or "")[:255] for k in META_FIELD_KEYS}


# ─── Serializer ──────────────────────────────────────────────────────────
class PumpHydraulicSnapshotSerializer(serializers.ModelSerializer):
    meta = serializers.SerializerMethodField()

    class Meta:
        model = PumpHydraulicSnapshot
        fields = [
            "id", "project_key", "label", "source",
            "project_title", "job_no", "client_name", "pump_tag_no", "calculation_no",
            "form_state", "context", "meta",
            "created_at", "updated_at",
        ]
        read_only_fields = (
            "id", "project_key", "project_title", "job_no", "client_name",
            "pump_tag_no", "calculation_no", "meta", "created_at", "updated_at",
        )

    def get_meta(self, obj):
        return {k: getattr(obj, k, "") or "" for k in META_FIELD_KEYS}

    def validate_form_state(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("form_state must be an object.")
        # Cap raw payload size to prevent abuse — soft-coded.
        approx_bytes = len(str(value))
        if approx_bytes > 1_500_000:  # ~1.5 MB after stringification
            raise serializers.ValidationError("form_state is too large.")
        return value

    def create(self, validated_data):
        request = self.context.get("request")
        form_state = validated_data.get("form_state") or {}
        validated_data["user"] = request.user
        validated_data["project_key"] = _derive_project_key(form_state)
        for k, v in _extract_meta(form_state).items():
            validated_data[k] = v
        instance = super().create(validated_data)
        # Prune oldest beyond per-user cap (idempotent, cheap).
        qs = PumpHydraulicSnapshot.objects.filter(user=request.user).order_by("-created_at")
        ids = list(qs.values_list("id", flat=True)[MAX_SNAPSHOTS_PER_USER:])
        if ids:
            PumpHydraulicSnapshot.objects.filter(id__in=ids).delete()
        return instance


# ─── ViewSet ─────────────────────────────────────────────────────────────
class PumpHydraulicSnapshotViewSet(viewsets.ModelViewSet):
    """
    /api/v1/process-datasheet/pump-hydraulic-snapshots/
        GET    list   (own snapshots, optionally ?project_key=…)
        POST   create
        GET    retrieve
        DELETE destroy
    /api/v1/process-datasheet/pump-hydraulic-snapshots/projects/
        GET project buckets w/ snapshot counts
    /api/v1/process-datasheet/pump-hydraulic-snapshots/clear/
        DELETE wipe all (or one project via ?project_key=…)
    """
    serializer_class = PumpHydraulicSnapshotSerializer
    permission_classes = [IsAuthenticated]
    http_method_names = ["get", "post", "delete", "head", "options"]

    def get_queryset(self):
        qs = PumpHydraulicSnapshot.objects.filter(user=self.request.user)
        project_key = self.request.query_params.get("project_key")
        if project_key:
            qs = qs.filter(project_key=project_key)
        limit = self.request.query_params.get("limit")
        if limit and limit.isdigit():
            qs = qs[: int(limit)]
        return qs

    @action(detail=False, methods=["get"], url_path="projects")
    def projects(self, request):
        qs = (
            PumpHydraulicSnapshot.objects.filter(user=request.user)
            .values("project_key")
            .annotate(count=Count("id"), last_saved_at=Max("created_at"))
            .order_by("-last_saved_at")
        )
        return Response(list(qs))

    @action(detail=False, methods=["delete"], url_path="clear")
    def clear(self, request):
        qs = PumpHydraulicSnapshot.objects.filter(user=request.user)
        project_key = request.query_params.get("project_key")
        if project_key:
            qs = qs.filter(project_key=project_key)
        deleted, _ = qs.delete()
        return Response({"deleted": deleted}, status=status.HTTP_200_OK)
