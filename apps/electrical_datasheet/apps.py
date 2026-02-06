from django.apps import AppConfig


class ElectricalDatasheetConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.electrical_datasheet'
    verbose_name = 'Electrical Datasheet'
    
    def ready(self):
        """
        Application initialization
        Import signals or perform setup when app is ready
        """
        pass

