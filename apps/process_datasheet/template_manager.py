"""
SDV Streams Template Manager
Bundled templates stored with the application code
"""
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class SDVTemplateManager:
    """
    Manager for SDV datasheet templates.
    
    Templates are bundled with the application code in:
    apps/process_datasheet/templates/sdv_streams/
    
    This means:
    - No S3 dependency
    - Works locally and in production (Railway)
    - Everyone gets the same template via git
    - No configuration needed
    
    Usage:
        # Get template path for reading
        template_path = SDVTemplateManager.get_template_path()
        
        # Open template file
        with SDVTemplateManager.get_template() as f:
            # Process template
            pass
        
        # Check if template exists
        if SDVTemplateManager.template_exists():
            print("Template ready!")
    """
    
    # Template directory bundled with the app
    BUNDLED_TEMPLATE_DIR = Path(__file__).parent / 'templates' / 'sdv_streams'
    DEFAULT_TEMPLATE = 'sdv_datasheet_template.xlsx'
    
    @classmethod
    def get_template_path(cls, template_name=None):
        """
        Get absolute path to the bundled template.
        
        Args:
            template_name (str, optional): Template filename. Defaults to 'sdv_datasheet_template.xlsx'
        
        Returns:
            Path: Absolute path to template file
        
        Raises:
            FileNotFoundError: If template doesn't exist
        """
        if template_name is None:
            template_name = cls.DEFAULT_TEMPLATE
        
        template_path = cls.BUNDLED_TEMPLATE_DIR / template_name
        
        if not template_path.exists():
            available = cls.list_templates()
            logger.error(
                f"[SDVTemplateManager] Template not found: {template_path}\n"
                f"Available templates: {available}"
            )
            raise FileNotFoundError(
                f"Template '{template_name}' not found. "
                f"Available: {available}"
            )
        
        logger.info(f"[SDVTemplateManager] Template path: {template_path}")
        return template_path
    
    @classmethod
    def get_template(cls, template_name=None):
        """
        Open and return the template file object.
        
        Args:
            template_name (str, optional): Template filename
        
        Returns:
            file object: Opened template file (remember to close it!)
        
        Usage:
            with SDVTemplateManager.get_template() as f:
                content = f.read()
        """
        template_path = cls.get_template_path(template_name)
        
        try:
            file_obj = open(template_path, 'rb')
            logger.info(f"[SDVTemplateManager] ✅ Opened template: {template_path.name}")
            return file_obj
        except Exception as e:
            logger.error(f"[SDVTemplateManager] ❌ Error opening template: {e}")
            raise
    
    @classmethod
    def template_exists(cls, template_name=None):
        """
        Check if template exists in bundled directory.
        
        Args:
            template_name (str, optional): Template filename
        
        Returns:
            bool: True if exists, False otherwise
        """
        if template_name is None:
            template_name = cls.DEFAULT_TEMPLATE
        
        template_path = cls.BUNDLED_TEMPLATE_DIR / template_name
        exists = template_path.exists()
        
        logger.info(f"[SDVTemplateManager] Template '{template_name}' exists: {exists}")
        return exists
    
    @classmethod
    def list_templates(cls):
        """
        List all bundled templates.
        
        Returns:
            list: List of template filenames
        """
        try:
            if not cls.BUNDLED_TEMPLATE_DIR.exists():
                logger.warning(
                    f"[SDVTemplateManager] Template directory not found: {cls.BUNDLED_TEMPLATE_DIR}"
                )
                return []
            
            files = [
                f.name for f in cls.BUNDLED_TEMPLATE_DIR.iterdir() 
                if f.is_file() and not f.name.startswith('.')
            ]
            
            logger.info(f"[SDVTemplateManager] Found {len(files)} templates: {files}")
            return files
        
        except Exception as e:
            logger.error(f"[SDVTemplateManager] Error listing templates: {e}")
            return []
    
    @classmethod
    def get_template_info(cls, template_name=None):
        """
        Get information about a template.
        
        Args:
            template_name (str, optional): Template filename
        
        Returns:
            dict: Template details (name, path, size, etc.)
        """
        if template_name is None:
            template_name = cls.DEFAULT_TEMPLATE
        
        try:
            template_path = cls.get_template_path(template_name)
            stat = template_path.stat()
            
            info = {
                'name': template_name,
                'path': str(template_path),
                'size_bytes': stat.st_size,
                'size_mb': round(stat.st_size / (1024 * 1024), 2),
                'exists': True,
                'location': 'bundled'
            }
            
            logger.info(f"[SDVTemplateManager] Template info: {info}")
            return info
        
        except FileNotFoundError:
            return {
                'name': template_name,
                'exists': False,
                'error': 'Template not found'
            }
        except Exception as e:
            logger.error(f"[SDVTemplateManager] Error getting template info: {e}")
            return {
                'name': template_name,
                'exists': False,
                'error': str(e)
            }
    
    @classmethod
    def save_template(cls, file_obj, template_name=None):
        """
        Save/update a template in the bundled directory.
        
        ⚠️  This modifies files in the codebase!
        Remember to commit the updated template to git.
        
        Args:
            file_obj: File object or Django UploadedFile
            template_name (str, optional): Template filename
        
        Returns:
            str: Path where template was saved
        """
        if template_name is None:
            template_name = cls.DEFAULT_TEMPLATE
        
        # Ensure directory exists
        cls.BUNDLED_TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
        
        template_path = cls.BUNDLED_TEMPLATE_DIR / template_name
        
        try:
            logger.info(f"[SDVTemplateManager] Saving template: {template_path}")
            
            with open(template_path, 'wb') as f:
                if hasattr(file_obj, 'chunks'):
                    # Django UploadedFile
                    for chunk in file_obj.chunks():
                        f.write(chunk)
                elif hasattr(file_obj, 'read'):
                    # Regular file object
                    file_obj.seek(0)
                    f.write(file_obj.read())
                else:
                    # Bytes
                    f.write(file_obj)
            
            logger.info(f"[SDVTemplateManager] ✅ Template saved: {template_path}")
            logger.warning(
                f"[SDVTemplateManager] 📝 Remember to commit this to git:\n"
                f"  git add {template_path}\n"
                f"  git commit -m 'Update SDV template'\n"
                f"  git push"
            )
            
            return str(template_path)
        
        except Exception as e:
            logger.error(f"[SDVTemplateManager] ❌ Error saving template: {e}")
            raise


# Example usage
if __name__ == "__main__":
    # Check template availability
    print(f"Default template exists: {SDVTemplateManager.template_exists()}")
    print(f"Available templates: {SDVTemplateManager.list_templates()}")
    
    # Get template info
    info = SDVTemplateManager.get_template_info()
    print(f"Template info: {info}")
    
    # Get template path for processing
    path = SDVTemplateManager.get_template_path()
    print(f"Template path: {path}")
