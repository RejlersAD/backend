from apps.rbac.apps import RbacConfig


class ReplicaRBACConfig(RbacConfig):
    """Exercise real authorization without unrelated engineering result signals."""

    def ready(self):
        import apps.rbac.signals  # noqa: F401
