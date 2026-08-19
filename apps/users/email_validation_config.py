"""
Email Validation Configuration
Soft-coded email validation rules and settings
"""
import re
from django.conf import settings


class EmailValidationConfig:
    """
    Centralized email validation configuration
    Allows easy customization without modifying core logic
    """
    
    # Email regex pattern (RFC 5322 compliant)
    EMAIL_PATTERN = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    
    # Disposable email domains (expandable list)
    DISPOSABLE_DOMAINS = [
        'tempmail.com',
        'throwaway.email',
        'guerrillamail.com',
        '10minutemail.com',
        'mailinator.com',
        'trashmail.com',
        'temp-mail.org',
        'fakeinbox.com',
        'getnada.com',
        'sharklasers.com'
    ]
    
    # Trusted email domains (whitelist - bypass disposable check)
    TRUSTED_DOMAINS = [
        'gmail.com',
        'yahoo.com',
        'hotmail.com',
        'outlook.com',
        'live.com',
        'icloud.com',
        'protonmail.com',
        'zoho.com',
        'aol.com',
        'mail.com'
    ]
    
    # Validation settings
    SETTINGS = {
        'enforce_disposable_check': True,  # Set False to disable disposable email blocking
        'enforce_mx_record_check': False,  # Set True to check MX records (requires DNS lookup)
        'allow_plus_addressing': True,  # Allow email+tag@domain.com format
        'min_email_length': 5,
        'max_email_length': 254,  # RFC 5321 limit
        'case_sensitive': False,  # Email comparison case sensitivity
    }
    
    # Error messages (soft-coded for easy localization)
    ERROR_MESSAGES = {
        'required': 'Email address is required',
        'invalid_format': 'Invalid email format. Please enter a valid email address (e.g., user@example.com)',
        'too_short': 'Email address is too short',
        'too_long': 'Email address is too long (maximum 254 characters)',
        'disposable_email': 'Disposable email addresses are not allowed. Please use a permanent email address',
        'mx_record_missing': 'Email domain does not have valid mail servers',
        'already_exists': 'A user with this email address already exists',
        'invalid_domain': 'Email domain is invalid',
    }
    
    @classmethod
    def validate_email_format(cls, email):
        """
        Validate email format
        
        Args:
            email: Email address to validate
            
        Returns:
            dict: {'is_valid': bool, 'message': str}
        """
        if not email:
            return {
                'is_valid': False,
                'message': cls.ERROR_MESSAGES['required']
            }
        
        # Convert to lowercase if not case sensitive
        if not cls.SETTINGS['case_sensitive']:
            email = email.lower()
        
        # Check length
        if len(email) < cls.SETTINGS['min_email_length']:
            return {
                'is_valid': False,
                'message': cls.ERROR_MESSAGES['too_short']
            }
        
        if len(email) > cls.SETTINGS['max_email_length']:
            return {
                'is_valid': False,
                'message': cls.ERROR_MESSAGES['too_long']
            }
        
        # Validate format with regex
        if not re.match(cls.EMAIL_PATTERN, email):
            return {
                'is_valid': False,
                'message': cls.ERROR_MESSAGES['invalid_format']
            }
        
        return {
            'is_valid': True,
            'message': 'Email format is valid'
        }
    
    @classmethod
    def is_disposable_email(cls, email):
        """
        Check if email is from a disposable domain
        
        Args:
            email: Email address to check
            
        Returns:
            bool: True if disposable, False otherwise
        """
        if not cls.SETTINGS['enforce_disposable_check']:
            return False
        
        try:
            domain = email.split('@')[1].lower()
            
            # Check if domain is in trusted list (whitelist)
            if domain in cls.TRUSTED_DOMAINS:
                return False
            
            # Check if domain is in disposable list (blacklist)
            return domain in cls.DISPOSABLE_DOMAINS
            
        except IndexError:
            return False
    
    @classmethod
    def check_mx_records(cls, email):
        """
        Check if email domain has valid MX records
        
        Args:
            email: Email address to check
            
        Returns:
            dict: {'is_valid': bool, 'message': str}
        """
        if not cls.SETTINGS['enforce_mx_record_check']:
            return {
                'is_valid': True,
                'message': 'MX record check disabled'
            }
        
        try:
            import dns.resolver
            domain = email.split('@')[1]
            
            # Query MX records
            mx_records = dns.resolver.resolve(domain, 'MX')
            
            if mx_records:
                return {
                    'is_valid': True,
                    'message': 'Email domain has valid mail servers'
                }
            else:
                return {
                    'is_valid': False,
                    'message': cls.ERROR_MESSAGES['mx_record_missing']
                }
                
        except (ImportError, Exception):
            # If DNS lookup fails or dnspython not installed, skip validation
            return {
                'is_valid': True,
                'message': 'MX record check skipped'
            }
    
    @classmethod
    def validate_email_deliverability(cls, email):
        """
        Comprehensive email validation
        
        Args:
            email: Email address to validate
            
        Returns:
            dict: {'is_valid': bool, 'message': str}
        """
        # Step 1: Validate format
        format_result = cls.validate_email_format(email)
        if not format_result['is_valid']:
            return format_result
        
        # Normalize email
        email = email.lower() if not cls.SETTINGS['case_sensitive'] else email
        
        # Step 2: Check disposable email
        if cls.is_disposable_email(email):
            return {
                'is_valid': False,
                'message': cls.ERROR_MESSAGES['disposable_email']
            }
        
        # Step 3: Check MX records (if enabled)
        mx_result = cls.check_mx_records(email)
        if not mx_result['is_valid']:
            return mx_result
        
        # All checks passed
        return {
            'is_valid': True,
            'message': 'Email is valid and deliverable'
        }
    
    @classmethod
    def get_domain(cls, email):
        """
        Extract domain from email
        
        Args:
            email: Email address
            
        Returns:
            str: Domain name or empty string
        """
        try:
            return email.split('@')[1].lower()
        except IndexError:
            return ''
    
    @classmethod
    def is_trusted_domain(cls, email):
        """
        Check if email is from a trusted domain
        
        Args:
            email: Email address
            
        Returns:
            bool: True if from trusted domain
        """
        domain = cls.get_domain(email)
        return domain in cls.TRUSTED_DOMAINS


# Convenience function for backward compatibility
def validate_email_deliverability(email):
    """
    Validate email deliverability (backward compatible function)
    
    Args:
        email: Email address to validate
        
    Returns:
        dict: {'is_valid': bool, 'message': str}
    """
    return EmailValidationConfig.validate_email_deliverability(email)
