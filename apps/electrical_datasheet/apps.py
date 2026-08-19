from django.apps import AppConfig


class ElectricalDatasheetConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.electrical_datasheet'
    verbose_name = 'Electrical Datasheet'
    
    def ready(self):
        """
        Application initialization — connect post_migrate signal to seed equipment types.
        Uses post_migrate signal (not direct DB access) to comply with Django best practices.
        """
        from django.db.models.signals import post_migrate
        post_migrate.connect(self._seed_equipment_types_signal, sender=self)

    @staticmethod
    def _seed_equipment_types_signal(sender, **kwargs):
        """Seed ElectricalEquipmentType records after migrations (safe DB access)."""
        try:
            from .equipment_types_config import EQUIPMENT_TYPES_CONFIG
            from .models import ElectricalEquipmentType

            for cfg in EQUIPMENT_TYPES_CONFIG:
                ElectricalEquipmentType.objects.get_or_create(
                    id=cfg['id'],
                    defaults={
                        'name': cfg.get('name', cfg['id']),
                        'code': cfg.get('code', cfg['id'].upper()[:5]),
                        'description': cfg.get('description', ''),
                        'icon': cfg.get('icon', ''),
                        'category': cfg.get('category', 'Electrical Equipment'),
                        'standards': cfg.get('standards', []),
                        'sections': cfg.get('sections', []),
                        'is_active': True,
                    }
                )
        except Exception:
            pass  # Silently skip if table doesn't exist yet

