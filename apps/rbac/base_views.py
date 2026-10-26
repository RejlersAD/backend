"""
Shared RBAC-enforcing base views.

Every business ViewSet / APIView must inherit from RBACViewSetMixin (or
RBACAPIView) so that access is *always* evaluated through:

    User -> Role -> Module -> Permission

No endpoint may rely on authentication alone.
"""
from rest_framework import viewsets
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated

from .route_guard import ModuleActionGuardMixin
from .permissions import HasModuleAccess


class RBACViewSetMixin(ModuleActionGuardMixin, viewsets.ViewSet):
    """
    Base mixin for all business ViewSets.

    Guarantees:
      * ModuleActionGuardMixin runs for every non-GET request.
      * HasModuleAccess / HasPermission are the only authorization source.
      * IsAuthenticated is the baseline (no anonymous business access).
    """
    permission_classes = [IsAuthenticated]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Force HasModuleAccess onto every subclass so no ViewSet can
        # accidentally drop RBAC by overriding permission_classes.
        if cls.permission_classes is None:
            cls.permission_classes = [IsAuthenticated, HasModuleAccess]
        elif not any(
            issubclass(p, (HasModuleAccess, ModuleActionGuardMixin))
            for p in cls.permission_classes
        ):
            cls.permission_classes = list(cls.permission_classes) + [
                IsAuthenticated,
                HasModuleAccess,
            ]


class RBACViewSet(RBACViewSetMixin, viewsets.ViewSet):
    """Concrete read/write ViewSet with mandatory RBAC."""


class RBACModelViewSet(RBACViewSetMixin, viewsets.ModelViewSet):
    """Concrete ModelViewSet with mandatory RBAC."""


class RBACReadOnlyModelViewSet(RBACViewSetMixin, viewsets.ReadOnlyModelViewSet):
    """Concrete ReadOnlyModelViewSet with mandatory RBAC."""


class RBACAPIView(ModuleActionGuardMixin, APIView):
    """Base APIView with mandatory RBAC for standalone endpoints."""
    permission_classes = [IsAuthenticated, HasModuleAccess]
