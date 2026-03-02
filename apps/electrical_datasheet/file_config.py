"""
Soft-coded configuration for electrical datasheet file handling
This configuration allows easy modification of accepted file types without code changes
"""

# Allowed file types configuration
ALLOWED_FILE_TYPES = {
    'pdf': {
        'extension': '.pdf',
        'mime_types': ['application/pdf'],
        'description': 'PDF documents',
        'max_size_mb': 15,
        'processing_method': 'extract_text_from_pdf'
    },
    'excel': {
        'extensions': ['.xls', '.xlsx', '.xlsm'],
        'mime_types': [
            'application/vnd.ms-excel',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.ms-excel.sheet.macroEnabled.12',
            'application/vnd.ms-excel.sheet.macroenabled.12'  # Lowercase variant for browser compatibility
        ],
        'description': 'Excel spreadsheets (XLS, XLSX, XLSM)',
        'max_size_mb': 20,
        'processing_method': 'extract_data_from_excel'
    },
    'image': {
        'extensions': ['.png', '.jpg', '.jpeg'],
        'mime_types': ['image/png', 'image/jpeg', 'image/jpg'],
        'description': 'Image files (PNG, JPG, JPEG)',
        'max_size_mb': 10,
        'processing_method': 'extract_text_from_image'
    }
}

# Maximum file size in bytes (default: 20MB)
MAX_FILE_SIZE = 20 * 1024 * 1024

# Supported file extensions (flat list for quick checking)
SUPPORTED_EXTENSIONS = []
for file_type_config in ALLOWED_FILE_TYPES.values():
    if 'extension' in file_type_config:
        SUPPORTED_EXTENSIONS.append(file_type_config['extension'])
    if 'extensions' in file_type_config:
        SUPPORTED_EXTENSIONS.extend(file_type_config['extensions'])

# Supported MIME types (flat list for quick checking)
SUPPORTED_MIME_TYPES = []
for file_type_config in ALLOWED_FILE_TYPES.values():
    SUPPORTED_MIME_TYPES.extend(file_type_config['mime_types'])


def get_file_type_config(file_extension):
    """
    Get configuration for a specific file extension
    
    Args:
        file_extension: File extension with or without dot (e.g., '.pdf' or 'pdf')
    
    Returns:
        dict: Configuration for the file type, or None if not supported
    """
    # Normalize extension
    if not file_extension.startswith('.'):
        file_extension = '.' + file_extension
    
    file_extension = file_extension.lower()
    
    # Search in configuration
    for file_type, config in ALLOWED_FILE_TYPES.items():
        if 'extension' in config and config['extension'] == file_extension:
            return {**config, 'type': file_type}
        if 'extensions' in config and file_extension in config['extensions']:
            return {**config, 'type': file_type}
    
    return None


def is_file_type_supported(file_extension):
    """
    Check if a file extension is supported
    
    Args:
        file_extension: File extension with or without dot
    
    Returns:
        bool: True if supported, False otherwise
    """
    return get_file_type_config(file_extension) is not None


def get_supported_extensions_display():
    """
    Get a human-readable string of supported extensions
    
    Returns:
        str: Comma-separated list of extensions
    """
    return ', '.join(SUPPORTED_EXTENSIONS)


def get_allowed_file_types_for_frontend():
    """
    Get file type configuration formatted for frontend use
    
    Returns:
        dict: Configuration suitable for frontend validation
    """
    frontend_config = {
        'extensions': SUPPORTED_EXTENSIONS,
        'mime_types': SUPPORTED_MIME_TYPES,
        'max_size_mb': MAX_FILE_SIZE / (1024 * 1024),
        'descriptions': []
    }
    
    for file_type, config in ALLOWED_FILE_TYPES.items():
        frontend_config['descriptions'].append(config['description'])
    
    return frontend_config


def validate_file_upload(file_name, file_size, mime_type=None):
    """
    Validate a file upload against configuration rules
    
    Args:
        file_name: Name of the uploaded file
        file_size: Size of file in bytes
        mime_type: MIME type of the file (optional)
    
    Returns:
        tuple: (is_valid, error_message)
    """
    # Check file extension
    file_extension = '.' + file_name.lower().split('.')[-1]
    
    if not is_file_type_supported(file_extension):
        supported = get_supported_extensions_display()
        return False, f"Unsupported file type. Allowed types: {supported}"
    
    # Check file size
    if file_size > MAX_FILE_SIZE:
        max_size_mb = MAX_FILE_SIZE / (1024 * 1024)
        return False, f"File size exceeds maximum allowed size of {max_size_mb}MB"
    
    # Check specific file type limits
    file_config = get_file_type_config(file_extension)
    if file_config:
        type_max_size = file_config.get('max_size_mb', 20) * 1024 * 1024
        if file_size > type_max_size:
            return False, f"File size exceeds maximum allowed for {file_config['description']}: {file_config['max_size_mb']}MB"
    
    # Optionally validate MIME type
    if mime_type and mime_type not in SUPPORTED_MIME_TYPES:
        return False, f"Invalid MIME type: {mime_type}"
    
    return True, None


def get_processing_method(file_extension):
    """
    Get the processing method name for a file type
    
    Args:
        file_extension: File extension
    
    Returns:
        str: Method name to use for processing, or None
    """
    config = get_file_type_config(file_extension)
    return config.get('processing_method') if config else None
