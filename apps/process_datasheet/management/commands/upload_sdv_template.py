"""
Management command to upload SDV datasheet templates to bundled directory
Usage: python manage.py upload_sdv_template path/to/template.xlsx [--name custom_name.xlsx]

Note: This copies the template into the codebase. Remember to commit to git!
"""
from django.core.management.base import BaseCommand
from django.core.files import File
import os


class Command(BaseCommand):
    help = 'Upload SDV datasheet template to bundled templates directory'
    
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
                # Save template to bundled directory
                saved_path = SDVTemplateManager.save_template(f, template_name)
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f'✅ Template uploaded successfully!'
                    )
                )
                self.stdout.write(f'📁 Saved to: {saved_path}')
                
                # Get template info
                info = SDVTemplateManager.get_template_info(template_name)
                self.stdout.write(f'📊 Size: {info["size_mb"]} MB')
                
                # List all templates
                templates = SDVTemplateManager.list_templates()
                self.stdout.write(
                    self.style.SUCCESS(
                        f'\n📋 Available templates ({len(templates)}):'
                    )
                )
                for tmpl in templates:
                    self.stdout.write(f'  - {tmpl}')
                
                # Remind to commit
                self.stdout.write(
                    self.style.WARNING(
                        f'\n📝 Remember to commit this to git:\n'
                        f'  git add {saved_path}\n'
                        f'  git commit -m "Update SDV template"\n'
                        f'  git push'
                    )
                )
                
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f'❌ Error uploading template: {str(e)}')
            )
            raise
