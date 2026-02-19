"""
RADAI Unified S3 Folder Configuration
Standardized folder structure for all services

This configuration provides:
1. Consistent folder hierarchy across all services
2. Environment-based customization
3. Backward compatibility with existing folder structures
4. Smart path resolution for document types
"""

import os
from typing import Dict, Optional
from django.conf import settings


class UnifiedFolderConfig:
    """
    Centralized S3 folder structure configuration for RADAI platform
    Provides consistent organization across all services
    """
    
    # Unified folder structure - hierarchical organization
    UNIFIED_FOLDERS = {
        # Engineering Documents
        'engineering': {
            'base': 'documents/engineering/',
            'pid_drawings': 'documents/engineering/pid_drawings/',
            'pfd_files': 'documents/engineering/pfd_files/',
            'pfd_conversions': 'documents/engineering/pfd_conversions/',
            'specifications': 'documents/engineering/specifications/',
            'technical_reports': 'documents/engineering/reports/',
            'standards': 'documents/engineering/standards/',
            'philosophy': 'documents/engineering/philosophy/',
        },
        
        # User-specific content
        'users': {
            'base': 'documents/users/',
            'uploads': 'documents/users/{user_id}/uploads/',
            'exports': 'documents/users/{user_id}/exports/',
            'profile': 'documents/users/{user_id}/profile/',
            'history': 'documents/users/{user_id}/history/{year}/{month}/',
        },
        
        # System documents
        'system': {
            'base': 'documents/system/',
            'reports': 'documents/system/reports/',
            'templates': 'documents/system/templates/',
            'backups': 'documents/system/backups/',
            'logs': 'documents/system/logs/{year}/{month}/{day}/',
        },
        
        # CRS Documents
        'crs': {
            'base': 'documents/crs/',
            'documents': 'documents/crs/documents/',
            'templates': 'documents/crs/templates/',
            'exports': 'documents/crs/exports/',
        },
        
        # Static assets
        'static': {
            'base': 'static/',
            'css': 'static/css/',
            'js': 'static/js/', 
            'images': 'static/images/',
            'fonts': 'static/fonts/',
        },
        
        # Temporary/processing files
        'temp': {
            'base': 'temp/',
            'processing': 'temp/processing/',
            'uploads': 'temp/uploads/',
            'conversions': 'temp/conversions/',
        },
        
        # Smart Project Collections - Multi-disciplinary document organization
        'projects': {
            'base': 'projects/',
            'project_root': 'projects/{project_code}/',
            'disciplines': 'projects/{project_code}/disciplines/',
            'discipline_folder': 'projects/{project_code}/disciplines/{discipline}/',
            'document_type': 'projects/{project_code}/disciplines/{discipline}/{document_type}/',
            'archive': 'projects/{project_code}/archive/',
            'reports': 'projects/{project_code}/reports/',
            'shared': 'projects/shared/',
            'templates': 'projects/templates/',
        }
    }
    
    # Legacy folder mapping for backward compatibility
    LEGACY_FOLDER_MAPPING = {
        # Old core service folders → New unified folders
        'media/pid_drawings/': 'documents/engineering/pid_drawings/',
        'media/pid_reports/': 'documents/engineering/reports/',
        'media/crs_documents/': 'documents/crs/documents/', 
        'media/pfd_files/': 'documents/engineering/pfd_files/',
        'media/avatars/': 'documents/users/{user_id}/profile/',
        'media/exports/': 'documents/users/{user_id}/exports/',
        'media/temp/': 'temp/processing/',
        
        # Old DesignIQ folders → New unified folders
        'pid_documents/': 'documents/engineering/pid_drawings/',
        
        # Old user storage folders → New unified folders
        'users/{user_id}/uploads/': 'documents/users/{user_id}/uploads/',
        'users/{user_id}/exports/': 'documents/users/{user_id}/exports/',
        'users/{user_id}/history/': 'documents/users/{user_id}/history/{year}/{month}/',
    }
    
    @classmethod
    def get_folder_path(cls, folder_type: str, **kwargs) -> str:
        """
        Get standardized folder path for a given document type
        
        Args:
            folder_type: Type of folder (e.g., 'pid_drawings', 'user_uploads')
            **kwargs: Additional parameters for path formatting (user_id, year, month, etc.)
            
        Returns:
            str: Standardized S3 folder path
        """
        # Check if it's a direct match in unified folders
        for category, folders in cls.UNIFIED_FOLDERS.items():
            if folder_type in folders:
                path = folders[folder_type]
                return path.format(**kwargs) if kwargs else path
        
        # Fallback to base folder if specific type not found
        for category, folders in cls.UNIFIED_FOLDERS.items():
            if folder_type.startswith(category):
                path = folders.get('base', f'documents/{category}/')
                return path.format(**kwargs) if kwargs else path
                
        # Default fallback
        return f'documents/general/{folder_type}/'
    
    @classmethod
    def resolve_legacy_path(cls, legacy_path: str, **kwargs) -> str:
        """
        Convert legacy folder path to unified structure
        
        Args:
            legacy_path: Old folder path
            **kwargs: Formatting parameters
            
        Returns:
            str: New unified folder path
        """
        if legacy_path in cls.LEGACY_FOLDER_MAPPING:
            unified_path = cls.LEGACY_FOLDER_MAPPING[legacy_path] 
            return unified_path.format(**kwargs) if kwargs else unified_path
        
        return legacy_path  # Return as-is if no mapping found
    
    @classmethod 
    def get_document_type_folder(cls, document_type: str, user_id: Optional[int] = None, **kwargs) -> str:
        """
        Get appropriate folder for a specific document type
        
        Args:
            document_type: Type of document
            user_id: User ID if user-specific document
            **kwargs: Additional parameters like project_code, discipline for project documents
            
        Returns:
            str: Appropriate folder path
        """
        type_mapping = {
            # Existing document types
            'pid_drawing': 'engineering.pid_drawings',
            'pfd_document': 'engineering.pfd_files',
            'pfd_conversion': 'engineering.pfd_conversions',
            'engineering_document': 'engineering.specifications',
            'user_upload': 'users.uploads',
            'user_export': 'users.exports',
            'system_report': 'system.reports',
            'crs_document': 'crs.documents',
            'template': 'system.templates',
            
            # Smart Project Collection document types
            'project_document': 'projects.document_type',
            'datasheet': 'projects.document_type',
            'process_flow': 'projects.document_type', 
            'loop_diagram': 'projects.document_type',
            'isometric_drawing': 'projects.document_type',
            'electrical_drawing': 'projects.document_type',
            'mechanical_drawing': 'projects.document_type',
            'structural_drawing': 'projects.document_type',
            'instrument_datasheet': 'projects.document_type',
            'equipment_datasheet': 'projects.document_type',
            'pump_curve': 'projects.document_type',
            'motor_datasheet': 'projects.document_type',
            'cable_schedule': 'projects.document_type',
            'io_list': 'projects.document_type',
            'foundation_plan': 'projects.document_type',
            'piping_spec': 'projects.document_type',
            'stress_analysis': 'projects.document_type',
            'heat_balance': 'projects.document_type',
        }
        
        folder_key = type_mapping.get(document_type, 'system.reports')
        category, folder_type = folder_key.split('.')
        
        folder_path = cls.UNIFIED_FOLDERS[category][folder_type]
        
        # Format with parameters if needed
        format_params = {}
        if user_id:
            format_params['user_id'] = user_id
        
        # Add kwargs (project_code, discipline, document_type, etc.)
        format_params.update(kwargs)
            
        if format_params and any('{' in folder_path for param in str(format_params.values())):
            try:
                folder_path = folder_path.format(**format_params)
            except KeyError as e:
                # If formatting fails, return the original path
                pass
            
        return folder_path
    
    @classmethod
    def get_project_folder(cls, project_code: str, discipline: Optional[str] = None, 
                          document_type: Optional[str] = None) -> str:
        """
        Get Smart Project Collection folder path for organized document storage
        
        Args:
            project_code: Project identifier (e.g., 'ADNOC-P16093')
            discipline: Engineering discipline (process, mechanical, electrical, etc.)
            document_type: Specific document type (datasheet, pid_drawing, etc.)
            
        Returns:
            str: Project-organized folder path
        """
        if document_type and discipline:
            # Full path: projects/PROJECT-CODE/disciplines/DISCIPLINE/DOCUMENT_TYPE/
            return cls.get_folder_path('document_type', 
                                     project_code=project_code,
                                     discipline=discipline, 
                                     document_type=document_type)
        elif discipline:
            # Discipline path: projects/PROJECT-CODE/disciplines/DISCIPLINE/
            return cls.get_folder_path('discipline_folder',
                                     project_code=project_code,
                                     discipline=discipline)
        else:
            # Project root: projects/PROJECT-CODE/
            return cls.get_folder_path('project_root', project_code=project_code)
    
    @classmethod
    def get_smart_document_path(cls, filename: str, project_code: str, discipline: str, 
                               document_type: str) -> str:
        """
        Get complete S3 key path for smart-organized document
        
        Args:
            filename: Original filename
            project_code: Project identifier
            discipline: Engineering discipline 
            document_type: Document type classification
            
        Returns:
            str: Complete S3 key path for the document
        """
        folder_path = cls.get_project_folder(project_code, discipline, document_type)
        return f"{folder_path}{filename}"
    
    @classmethod
    def is_project_document(cls, s3_key: str) -> bool:
        """
        Check if an S3 key belongs to the Smart Project Collection system
        
        Args:
            s3_key: S3 object key
            
        Returns:
            bool: True if document is part of project organization
        """
        return s3_key.startswith('projects/') and '/disciplines/' in s3_key
    
    @classmethod
    def parse_project_path(cls, s3_key: str) -> Dict[str, Optional[str]]:
        """
        Parse project information from S3 key
        
        Args:
            s3_key: S3 object key from project organization
            
        Returns:
            dict: Parsed project information (project_code, discipline, document_type, filename)
        """
        if not cls.is_project_document(s3_key):
            return {'project_code': None, 'discipline': None, 'document_type': None, 'filename': None}
        
        try:
            # Expected format: projects/{project_code}/disciplines/{discipline}/{document_type}/{filename}
            parts = s3_key.split('/')
            
            if len(parts) >= 5 and parts[0] == 'projects' and parts[2] == 'disciplines':
                project_code = parts[1]
                discipline = parts[3]
                document_type = parts[4] if len(parts) > 4 else None
                filename = parts[-1] if len(parts) > 5 else None
                
                return {
                    'project_code': project_code,
                    'discipline': discipline, 
                    'document_type': document_type,
                    'filename': filename
                }
        except (IndexError, AttributeError):
            pass
            
        return {'project_code': None, 'discipline': None, 'document_type': None, 'filename': None}
    
    @classmethod
    def get_environment_config(cls) -> Dict[str, str]:
        """
        Get environment-specific folder configuration
        
        Returns:
            dict: Environment configuration overrides
        """
        env_config = {}
        
        # Check for environment-specific overrides
        folder_prefix = os.getenv('RADAI_FOLDER_PREFIX', '')
        if folder_prefix:
            env_config['folder_prefix'] = folder_prefix
            
        # Development/staging folder separation
        environment = os.getenv('DJANGO_ENV', 'development')
        if environment in ['staging', 'test']:
            env_config['environment_prefix'] = f'{environment}/'
            
        return env_config


# Convenience functions for easy usage
def get_folder(folder_type: str, **kwargs) -> str:
    """Get standardized folder path"""
    return UnifiedFolderConfig.get_folder_path(folder_type, **kwargs)

def get_document_folder(document_type: str, user_id: Optional[int] = None, **kwargs) -> str:
    """Get folder for document type"""
    return UnifiedFolderConfig.get_document_type_folder(document_type, user_id, **kwargs)

def resolve_legacy_folder(legacy_path: str, **kwargs) -> str:
    """Convert legacy path to unified structure"""
    return UnifiedFolderConfig.resolve_legacy_path(legacy_path, **kwargs)

# Smart Project Collection convenience functions
def get_project_folder(project_code: str, discipline: Optional[str] = None, 
                      document_type: Optional[str] = None) -> str:
    """Get Smart Project Collection folder path"""
    return UnifiedFolderConfig.get_project_folder(project_code, discipline, document_type)

def get_smart_document_path(filename: str, project_code: str, discipline: str, document_type: str) -> str:
    """Get complete S3 path for smart-organized document"""
    return UnifiedFolderConfig.get_smart_document_path(filename, project_code, discipline, document_type)

def is_project_document(s3_key: str) -> bool:
    """Check if document belongs to Smart Project Collection"""
    return UnifiedFolderConfig.is_project_document(s3_key)

def parse_project_path(s3_key: str) -> Dict[str, Optional[str]]:
    """Parse project information from S3 key"""
    return UnifiedFolderConfig.parse_project_path(s3_key)


# Configuration validation
def validate_folder_config():
    """Validate folder configuration consistency"""
    issues = []
    
    # Check for duplicate paths (skip template paths with parameters)
    all_paths = []
    for category, folders in UnifiedFolderConfig.UNIFIED_FOLDERS.items():
        for folder_type, path in folders.items():
            if '{' not in path and path in all_paths:
                issues.append(f"Duplicate path: {path}")
            all_paths.append(path)
    
    # Validate project structure templates
    project_folders = UnifiedFolderConfig.UNIFIED_FOLDERS.get('projects', {})
    required_project_templates = ['project_root', 'disciplines', 'discipline_folder', 'document_type']
    
    for template in required_project_templates:
        if template not in project_folders:
            issues.append(f"Missing required project template: {template}")
        elif '{project_code}' not in project_folders.get(template, ''):
            issues.append(f"Project template missing project_code parameter: {template}")
    
    # Check legacy mappings point to valid unified paths  
    for legacy, unified in UnifiedFolderConfig.LEGACY_FOLDER_MAPPING.items():
        if unified not in all_paths and not any('{' in unified for unified in [unified]):
            issues.append(f"Legacy mapping points to invalid path: {legacy} -> {unified}")
    
    # Validate Smart Project Collection document types
    smart_doc_types = ['datasheet', 'pid_drawing', 'process_flow', 'loop_diagram', 'isometric_drawing']
    for doc_type in smart_doc_types:
        folder_path = UnifiedFolderConfig.get_document_type_folder(doc_type)
        if 'projects/' not in folder_path:
            issues.append(f"Smart document type not properly mapped to projects: {doc_type}")
    
    if issues:
        print("⚠️  Folder configuration issues:")
        for issue in issues:
            print(f"   - {issue}")
    else:
        print("✅ Folder configuration validated successfully")
        print("   ✅ Legacy folder compatibility maintained")
        print("   ✅ Smart Project Collection integration verified")
        print("   ✅ All document types properly mapped")


if __name__ == "__main__":
    # Run validation when executed directly
    validate_folder_config()
    
    # Example usage
    print("\n📁 Example folder paths:")
    print(f"PID Drawing: {get_folder('pid_drawings')}")
    print(f"User Upload: {get_folder('uploads', user_id=123)}")
    print(f"System Report: {get_document_folder('system_report')}")
    print(f"Legacy conversion: {resolve_legacy_folder('media/pid_drawings/')}")
    
    # Smart Project Collection examples
    print("\n🎯 Smart Project Collection examples:")
    print(f"Project Root: {get_project_folder('ADNOC-P16093')}")
    print(f"Discipline Folder: {get_project_folder('ADNOC-P16093', 'mechanical')}")
    print(f"Document Path: {get_smart_document_path('pump_datasheet.pdf', 'ADNOC-P16093', 'mechanical', 'datasheet')}")
    
    # Test path parsing
    test_s3_key = "projects/ADNOC-P16093/disciplines/mechanical/datasheet/pump_001.pdf"
    parsed = parse_project_path(test_s3_key)
    print(f"Parsed path: {parsed}")
    print(f"Is project document: {is_project_document(test_s3_key)}")