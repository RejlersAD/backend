"""
Password Policy Configuration
Centralized password policy settings for soft-coding best practices
"""
from datetime import timedelta

# ========================================================================
# PASSWORD EXPIRY SETTINGS
# ========================================================================

# Number of days before password expires
# 180 days (6 months) — NIST SP 800-63B / enterprise O&G tool standard.
# Avoids constant expiry friction on an internal engineering platform.
PASSWORD_EXPIRY_DAYS = 180

# Warning period before expiry (in days)
# 14 days gives users two full working weeks to act — avoids surprise lockouts.
PASSWORD_EXPIRY_WARNING_DAYS = 14

# Grace period after expiry (in days) before forcing logout
# 14 days allows users returning from leave/travel to still access the system.
PASSWORD_EXPIRY_GRACE_DAYS = 14

# Exempt superusers from password expiry
EXEMPT_SUPERUSERS_FROM_EXPIRY = True

# Exempt staff from password expiry
EXEMPT_STAFF_FROM_EXPIRY = False


# ========================================================================
# PASSWORD STRENGTH REQUIREMENTS
# ========================================================================

# Minimum password length
PASSWORD_MIN_LENGTH = 8

# Maximum password length
PASSWORD_MAX_LENGTH = 128

# Require uppercase letters
PASSWORD_REQUIRE_UPPERCASE = True

# Require lowercase letters
PASSWORD_REQUIRE_LOWERCASE = True

# Require numbers
PASSWORD_REQUIRE_NUMBERS = True

# Require special characters
PASSWORD_REQUIRE_SPECIAL = True

# Special characters allowed
PASSWORD_SPECIAL_CHARACTERS = '!@#$%^&*()_+-=[]{}|;:,.<>?'


# ========================================================================
# PASSWORD HISTORY
# ========================================================================

# Number of previous passwords to check
PASSWORD_HISTORY_COUNT = 5

# Prevent password reuse
PREVENT_PASSWORD_REUSE = True


# ========================================================================
# PASSWORD RESET SETTINGS
# ========================================================================

# Password reset token expiry (in hours)
PASSWORD_RESET_TOKEN_EXPIRY_HOURS = 24

# Maximum reset attempts per day
PASSWORD_RESET_MAX_ATTEMPTS_PER_DAY = 5


# ========================================================================
# NOTIFICATION SETTINGS
# ========================================================================

# Send email notification before password expires
SEND_EXPIRY_NOTIFICATION = True

# Days before expiry to send first notification
FIRST_NOTIFICATION_DAYS = 30

# Days before expiry to send second notification
SECOND_NOTIFICATION_DAYS = 14

# Days before expiry to send final notification
FINAL_NOTIFICATION_DAYS = 7


# ========================================================================
# HELPER FUNCTIONS
# ========================================================================

def get_password_expiry_timedelta():
    """Get password expiry as timedelta object"""
    return timedelta(days=PASSWORD_EXPIRY_DAYS)


def get_password_warning_timedelta():
    """Get warning period as timedelta object"""
    return timedelta(days=PASSWORD_EXPIRY_WARNING_DAYS)


def get_password_grace_timedelta():
    """Get grace period as timedelta object"""
    return timedelta(days=PASSWORD_EXPIRY_GRACE_DAYS)


def should_exempt_user(user):
    """
    Check if user should be exempt from password expiry
    
    Args:
        user: User model instance
        
    Returns:
        bool: True if user is exempt from password expiry
    """
    if EXEMPT_SUPERUSERS_FROM_EXPIRY and user.is_superuser:
        return True
    
    if EXEMPT_STAFF_FROM_EXPIRY and user.is_staff:
        return True
    
    return False


def get_password_expiry_status(user):
    """
    Get password expiry status for a user
    
    Args:
        user: User model instance
        
    Returns:
        dict: {
            'expired': bool,
            'days_until_expiry': int,
            'requires_change': bool,
            'in_grace_period': bool,
            'in_warning_period': bool
        }
    """
    from django.utils import timezone
    
    # Check if user is exempt
    if should_exempt_user(user):
        return {
            'expired': False,
            'days_until_expiry': None,
            'requires_change': False,
            'in_grace_period': False,
            'in_warning_period': False,
            'exempt': True
        }
    
    # Check if user must reset password
    if getattr(user, 'must_reset_password', False):
        return {
            'expired': True,
            'days_until_expiry': 0,
            'requires_change': True,
            'in_grace_period': False,
            'in_warning_period': False,
            'exempt': False
        }
    
    # Get last password change date
    last_change = getattr(user, 'last_password_change', None)
    
    if not last_change:
        # No password change recorded, use date_joined as fallback
        last_change = user.date_joined
    
    # Calculate time since last change
    now = timezone.now()
    time_since_change = now - last_change
    days_since_change = time_since_change.days
    
    # Calculate days until expiry
    days_until_expiry = PASSWORD_EXPIRY_DAYS - days_since_change
    
    # Check various states
    expired = days_until_expiry <= 0
    in_grace_period = expired and abs(days_until_expiry) <= PASSWORD_EXPIRY_GRACE_DAYS
    in_warning_period = not expired and days_until_expiry <= PASSWORD_EXPIRY_WARNING_DAYS
    requires_change = expired and not in_grace_period
    
    return {
        'expired': expired,
        'days_until_expiry': days_until_expiry,
        'requires_change': requires_change,
        'in_grace_period': in_grace_period,
        'in_warning_period': in_warning_period,
        'exempt': False
    }


def validate_password_strength(password):
    """
    Validate password against policy requirements
    
    Args:
        password: Password string to validate
        
    Returns:
        tuple: (is_valid, error_messages)
    """
    errors = []
    
    # Check length
    if len(password) < PASSWORD_MIN_LENGTH:
        errors.append(f'Password must be at least {PASSWORD_MIN_LENGTH} characters long')
    
    if len(password) > PASSWORD_MAX_LENGTH:
        errors.append(f'Password must be no more than {PASSWORD_MAX_LENGTH} characters long')
    
    # Check uppercase
    if PASSWORD_REQUIRE_UPPERCASE and not any(c.isupper() for c in password):
        errors.append('Password must contain at least one uppercase letter')
    
    # Check lowercase
    if PASSWORD_REQUIRE_LOWERCASE and not any(c.islower() for c in password):
        errors.append('Password must contain at least one lowercase letter')
    
    # Check numbers
    if PASSWORD_REQUIRE_NUMBERS and not any(c.isdigit() for c in password):
        errors.append('Password must contain at least one number')
    
    # Check special characters
    if PASSWORD_REQUIRE_SPECIAL and not any(c in PASSWORD_SPECIAL_CHARACTERS for c in password):
        errors.append(f'Password must contain at least one special character: {PASSWORD_SPECIAL_CHARACTERS}')
    
    return len(errors) == 0, errors
