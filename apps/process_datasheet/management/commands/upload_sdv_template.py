"""
Management command to upload SDV datasheet templates
Usage: python manage.py upload_sdv_template path/to/template.xlsx [--name custom_name.xlsx]
"""
from django.core.management.base import BaseCommand
from django.core.files import File
import os


class Command(BaseCommand):
    help = 'Upload SDV datasheet template to storage (S3 or local)'
    
    def add_arguments(self, parser):
        parser.add_argument(
            'template_path',
            type=str,
            help='Path to the template file (XLSX, XLS, or PDF)'
        )
        parser.add_argument(
            '--name',
            type=str,
            default=None,
            help='Custom name for the template (optional)'
        )
    
    def handle(self, *args, **options):
        template_path = options['template_path']
        template_name = options.get('name')
        
        # Check if file exists
        if not os.path.exists(template_path):
            self.stdout.write(
                self.style.ERROR(f'File not found: {template_path}')
            )
            return
        
        # Check file extension
        valid_extensions = ['.xlsx', '.xls', '.pdf']
        file_ext = os.path.splitext(template_path)[1].lower()
        if file_ext not in valid_extensions:
            self.stdout.write(
                self.style.ERROR(
                    f'Invalid file type. Must be one of: {", ".join(valid_extensions)}'
                )
            )
            return
        
        # Import here to avoid circular imports
        from apps.process_datasheet.template_manager import SDVTemplateManager
        
        try:
            with open(template_path, 'rb') as f:
                # Create Django File object
                file_obj = File(f)
                
                # Save template
                saved_path = SDVTemplateManager.save_template(file_obj, template_name)
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f'✅ Template uploaded successfully!'
                    )
                )
                self.stdout.write(f'📁 Saved to: {saved_path}')
                
                # Show template URL if S3
                from django.conf import settings
                if settings.USE_S3:
                    template_url = SDVTemplateManager.get_template_url(template_name)
                    self.stdout.write(f'🔗 S3 URL: {template_url}')
                
                # List all templates
                templates = SDVTemplateManager.list_templates()
                self.stdout.write(
                    self.style.SUCCESS(
                        f'\n📋 Available templates ({len(templates)}):'
                    )
                )
                for tmpl in templates:
                    self.stdout.write(f'  - {tmpl}')
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Error uploading template: {str(e)}')
            )
            raise
