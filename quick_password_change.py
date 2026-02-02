"""
Quick Password Change Script
Simple wrapper for changing user passwords
Set environment variables before running this script
"""
import os
import sys

# User configuration - CHANGE THESE VALUES
USER_EMAIL = "mitul.patel@rejlers.ae"
NEW_PASSWORD = "Mitul@123"

# Optional configurations
MUST_RESET = False  # Set to True if user must reset password on next login
IS_TEMP_PASSWORD = False  # Set to True if this is a temporary password

# ============================================================================
# You can also set these via command line arguments:
# python quick_password_change.py user@example.com NewPassword123
# ============================================================================

if len(sys.argv) >= 2:
    USER_EMAIL = sys.argv[1]
if len(sys.argv) >= 3:
    NEW_PASSWORD = sys.argv[2]

print(f"""
================================================================================
Quick Password Change Script
================================================================================
Target User: {USER_EMAIL}
Password Length: {len(NEW_PASSWORD)} characters
Must Reset: {MUST_RESET}
Temporary: {IS_TEMP_PASSWORD}
================================================================================

Running Django management command...
""")

# Build the command
cmd = f'python manage.py change_password --email "{USER_EMAIL}" --password "{NEW_PASSWORD}"'

if MUST_RESET:
    cmd += ' --must-reset'

if IS_TEMP_PASSWORD:
    cmd += ' --temp-password'

print(f"Command: {cmd}\n")
print("=" * 80)

# Execute the command
os.system(cmd)
