"""
SDV Streams Template Manager
Store and retrieve datasheet templates from S3/local storage
"""
import os
from pathlib import Path
from django.conf import settings
from django.core.files.storage import default_storage
import logging

logger = logging.getLogger(__name__)


class SDVTemplateManager:
    """Manage SDV datasheet templates"""
    
    TEMPLATE_DIR = 'templates/sdv_streams/'
    DEFAULT_TEMPLATE = 'sdv_datasheet_template.xlsx'
    
    @classmethod
    def get_template_path(cls, template_name=None):
        """Get path to template file"""
        template_name = template_name or cls.DEFAULT_TEMPLATE
        return os.path.join(cls.TEMPLATE_DIR, template_name)
    
    @classmethod
    def template_exists(cls, template_name=None):
        """Check if template exists in storage"""
        template_path = cls.get_template_path(template_name)
        
        if settings.USE_S3:
            return default_storage.exists(template_path)
        else:
            # Local storage
            full_path = os.path.join(settings.MEDIA_ROOT, template_path)
            return os.path.exists(full_path)
    
    @classmethod
    def get_template(cls, template_name=None):
        """
        Retrieve template file from storage
        Returns: file object or path
        """
        template_path = cls.get_template_path(template_name)
        
        if not cls.template_exists(template_name):
            logger.error(f"Template not found: {template_path}")
            raise FileNotFoundError(f"Template {template_name or cls.DEFAULT_TEMPLATE} not found")
        
        if settings.USE_S3:
            # Return S3 file object
            return default_storage.open(template_path, 'rb')
        else:
            # Return local file path
            full_path = os.path.join(settings.MEDIA_ROOT, template_path)
            return open(full_path, 'rb')
    
    @classmethod
    def save_template(cls, file_obj, template_name=None):
        """
        Save a new template to storage
        Args:
            file_obj: File object to save
            template_name: Name for the template (optional)
        Returns:
            str: Path where template was saved
        """
        template_name = template_name or cls.DEFAULT_TEMPLATE
        template_path = cls.get_template_path(template_name)
        
        if settings.USE_S3:
            # Save to S3
            saved_path = default_storage.save(template_path, file_obj)
            logger.info(f"Template saved to S3: {saved_path}")
            return saved_path
        else:
            # Save to local media directory
            full_path = os.path.join(settings.MEDIA_ROOT, template_path)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            
            with open(full_path, 'wb') as f:
                for chunk in file_obj.chunks():
                    f.write(chunk)
            
            logger.info(f"Template saved locally: {full_path}")
            return template_path
    
    @classmethod
    def list_templates(cls):
        """List all available templates"""
        if settings.USE_S3:
            # List from S3
            directories, files = default_storage.listdir(cls.TEMPLATE_DIR)
            return [f for f in files if f.endswith(('.xlsx', '.xls', '.pdf'))]
        else:
            # List from local storage
            template_dir = os.path.join(settings.MEDIA_ROOT, cls.TEMPLATE_DIR)
            if not os.path.exists(template_dir):
                return []
            
            return [
                f for f in os.listdir(template_dir)
                if f.endswith(('.xlsx', '.xls', '.pdf'))
            ]
    
    @classmethod
    def get_template_url(cls, template_name=None):
        """Get URL to access template (for S3)"""
        template_path = cls.get_template_path(template_name)
        
        if settings.USE_S3:
            return default_storage.url(template_path)
        else:
            return f"{settings.MEDIA_URL}{template_path}"


# Example usage in views:
"""
from apps.process_datasheet.template_manager import SDVTemplateManager

# In your extract view:
def extract_sdv_datasheet(request):
    pid_file = request.FILES.get('pid_file')
    hmb_file = request.FILES.get('hmb_file')
    
    # ... extract data from P&ID and HMB ...
    
    # Get stored template
    try:
        template_file = SDVTemplateManager.get_template()
        
        # Fill template with extracted data
        filled_datasheet = fill_template_with_data(
            template_file,
            extracted_data={
                'valves': valve_data,
                'streams': stream_data,
                'process_conditions': hmb_data
            }
        )
        
        # Return filled template to user
        return Response({
            'success': True,
            'datasheet_url': filled_datasheet_url,
            'message': 'Template filled successfully'
        })
        
    except FileNotFoundError:
        return Response({
            'success': False,
            'error': 'Template not found. Please contact administrator.'
        }, status=500)
"""


# Management command to upload template:
"""
# python manage.py upload_sdv_template path/to/template.xlsx

from django.core.management.base import BaseCommand
from apps.process_datasheet.template_manager import SDVTemplateManager

class Command(BaseCommand):
    help = 'Upload SDV datasheet template to storage'
    
    def add_arguments(self, parser):
        parser.add_argument('template_path', type=str)
        parser.add_argument('--name', type=str, default=None)
    
    def handle(self, *args, **options):
        template_path = options['template_path']
        template_name = options.get('name')
        
        with open(template_path, 'rb') as f:
            saved_path = SDVTemplateManager.save_template(f, template_name)
            self.stdout.write(
                self.style.SUCCESS(f'Template uploaded: {saved_path}')
            )
"""
