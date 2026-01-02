"""
Email templates for user management
Soft-coded email templates with dynamic content
"""

# Email template configurations
EMAIL_TEMPLATES = {
    'welcome': {
        'subject': 'Welcome to RADAI ! Powered by Rejlers Abu Dhabi',
        'html_template': '''
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #4F46E5; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
        .content {{ background-color: #f9fafb; padding: 30px; border: 1px solid #e5e7eb; }}
        .credentials {{ background-color: #fff; padding: 20px; margin: 20px 0; border-left: 4px solid #4F46E5; border-radius: 4px; }}
        .button {{ display: inline-block; padding: 12px 30px; background-color: #4F46E5; color: white; text-decoration: none; border-radius: 6px; margin: 20px 0; }}
        .footer {{ text-align: center; padding: 20px; color: #6b7280; font-size: 12px; }}
        .warning {{ background-color: #fef3c7; padding: 15px; margin: 20px 0; border-left: 4px solid #f59e0b; border-radius: 4px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Welcome to RADAI !</h1>
            <p style="margin: 5px 0 0 0; font-size: 14px;">Powered by Rejlers Abu Dhabi</p>
        </div>
        <div class="content">
            <h2>Hello {first_name} {last_name},</h2>
            <p>Your account has been successfully created. We're excited to have you on board!</p>
            
            <div class="credentials">
                <h3>Your Login Credentials:</h3>
                <p><strong>Email:</strong> {email}</p>
                <p><strong>Temporary Password:</strong> {temp_password}</p>
                <p><strong>Login URL:</strong> <a href="{login_url}">{login_url}</a></p>
            </div>
            
            <div class="warning">
                <strong>⚠️ Important Security Notice:</strong>
                <p>For security reasons, you will be required to change your password on your first login. This temporary password will only work for your initial sign-in.</p>
            </div>
            
            <p>Click the button below to access your account:</p>
            <a href="{login_url}" class="button">Login to Your Account</a>
            
            <h3>What's Next?</h3>
            <ul>
                <li>Login with the credentials above</li>
                <li>Create a strong, unique password</li>
                <li>Complete your profile setup</li>
                <li>Explore the features available to you</li>
            </ul>
            
            <p>If you have any questions or need assistance, please don't hesitate to contact our support team.</p>
            
            <p>Best regards,<br><strong>RADAI Team</strong><br>Powered by Rejlers Abu Dhabi</p>
        </div>
        <div class="footer">
            <p>This is an automated message. Please do not reply to this email.</p>
            <p>&copy; 2025 RADAI - Rejlers Abu Dhabi. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
        ''',
        'text_template': '''
Welcome to RADAI !
Powered by Rejlers Abu Dhabi

Hello {first_name} {last_name},

Your account has been successfully created. We're excited to have you on board!

YOUR LOGIN CREDENTIALS:
=======================
Email: {email}
Temporary Password: {temp_password}
Login URL: {login_url}

⚠️ IMPORTANT SECURITY NOTICE:
For security reasons, you will be required to change your password on your first login. 
This temporary password will only work for your initial sign-in.

WHAT'S NEXT?
============
1. Login with the credentials above
2. Create a strong, unique password
3. Complete your profile setup
4. Explore the features available to you

If you have any questions or need assistance, please don't hesitate to contact our support team.

RADAI Team
Powered by Rejlers Abu Dhabi

---
This is an automated message. Please do not reply to this email.
© 2025 RADAI - Rejlers Abu Dhabi
        '''
    },
    'welcome_with_setup': {
        'subject': 'Welcome to RADAI ! Set Up Your Password',
        'html_template': '''
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #4F46E5; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
        .content {{ background-color: #f9fafb; padding: 30px; border: 1px solid #e5e7eb; }}
        .credentials {{ background-color: #fff; padding: 20px; margin: 20px 0; border-left: 4px solid #4F46E5; border-radius: 4px; }}
        .button {{ display: inline-block; padding: 12px 30px; background-color: #4F46E5; color: white; text-decoration: none; border-radius: 6px; margin: 20px 0; }}
        .footer {{ text-align: center; padding: 20px; color: #6b7280; font-size: 12px; }}
        .info-box {{ background-color: #dbeafe; padding: 15px; margin: 20px 0; border-left: 4px solid #3b82f6; border-radius: 4px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Welcome to RADAI !</h1>
            <p style="margin: 5px 0 0 0; font-size: 14px;">Powered by Rejlers Abu Dhabi</p>
        </div>
        <div class="content">
            <h2>Hello {{first_name}} {{last_name}},</h2>
            <p>Your account has been successfully created! We're excited to have you on board.</p>
            
            <div class="credentials">
                <h3>📧 Your Email:</h3>
                <p><strong>{{email}}</strong></p>
            </div>
            
            <div class="info-box">
                <h3>🔒 Secure Password Setup Required</h3>
                <p>For your security, please click the button below to set up your own password. This link will remain valid for <strong>{{expiry_hours}} hours</strong>.</p>
            </div>
            
            <center>
                <a href="{{setup_url}}" class="button">Set Up Your Password</a>
            </center>
            
            <p style="font-size: 12px; color: #666;">If the button doesn't work, copy and paste this link into your browser:</p>
            <p style="font-size: 11px; word-break: break-all; color: #4F46E5;">{{setup_url}}</p>
            
            <h3>What's Next?</h3>
            <ul>
                <li>Click the link above to create your password</li>
                <li>Choose a strong, unique password (minimum 8 characters)</li>
                <li>Login to your account at <a href="{{login_url}}">{{login_url}}</a></li>
                <li>Complete your profile and explore RADAI features</li>
            </ul>
            
            <p>If you have any questions or need assistance, please don't hesitate to contact our support team.</p>
            
            <p>Best regards,<br><strong>RADAI Team</strong><br>Powered by Rejlers Abu Dhabi</p>
        </div>
        <div class="footer">
            <p>This is an automated message. Please do not reply to this email.</p>
            <p>&copy; 2025 RADAI - Rejlers Abu Dhabi. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
        ''',
        'text_template': '''
Welcome to RADAI !
Powered by Rejlers Abu Dhabi

Hello {{first_name}} {{last_name}},

Your account has been successfully created! We're excited to have you on board.

YOUR EMAIL:
===========
{{email}}

SECURE PASSWORD SETUP REQUIRED:
================================
For your security, please use the link below to set up your own password.
This link will remain valid for {{expiry_hours}} hours.

Set Up Password: {{setup_url}}

WHAT'S NEXT?
============
1. Click the link above to create your password
2. Choose a strong, unique password (minimum 8 characters)
3. Login to your account at {{login_url}}
4. Complete your profile and explore RADAI features

If you have any questions or need assistance, please don't hesitate to contact our support team.

Best regards,
RADAI Team
Powered by Rejlers Abu Dhabi

---
This is an automated message. Please do not reply to this email.
© 2025 RADAI - Rejlers Abu Dhabi
        '''
    },
    'password_reset': {
        'subject': 'Reset Your RADAI Password',
        'html_template': '''
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #4F46E5; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
        .content {{ background-color: #f9fafb; padding: 30px; border: 1px solid #e5e7eb; }}
        .button {{ display: inline-block; padding: 12px 30px; background-color: #4F46E5; color: white; text-decoration: none; border-radius: 6px; margin: 20px 0; }}
        .footer {{ text-align: center; padding: 20px; color: #6b7280; font-size: 12px; }}
        .warning {{ background-color: #fef3c7; padding: 15px; margin: 20px 0; border-left: 4px solid #f59e0b; border-radius: 4px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Password Reset Request</h1>
            <p style="margin: 5px 0 0 0; font-size: 14px;">RADAI - Powered by Rejlers Abu Dhabi</p>
        </div>
        <div class="content">
            <h2>Hello {{first_name}} {{last_name}},</h2>
            <p>We received a request to reset your password for your RADAI account.</p>
            
            <center>
                <a href="{{reset_url}}" class="button">Reset Your Password</a>
            </center>
            
            <p style="font-size: 12px; color: #666;">If the button doesn't work, copy and paste this link into your browser:</p>
            <p style="font-size: 11px; word-break: break-all; color: #4F46E5;">{{reset_url}}</p>
            
            <div class="warning">
                <strong>⚠️ Security Notice:</strong>
                <p>This password reset link will expire in <strong>{{expiry_hours}} hours</strong>.</p>
                <p>If you didn't request this password reset, please ignore this email. Your password will remain unchanged.</p>
            </div>
            
            <p>For security reasons:</p>
            <ul>
                <li>Never share this link with anyone</li>
                <li>Choose a strong, unique password</li>
                <li>Use a combination of letters, numbers, and special characters</li>
            </ul>
            
            <p>If you have any questions or concerns, please contact our support team immediately.</p>
            
            <p>Best regards,<br><strong>RADAI Team</strong><br>Powered by Rejlers Abu Dhabi</p>
        </div>
        <div class="footer">
            <p>This is an automated message. Please do not reply to this email.</p>
            <p>&copy; 2025 RADAI - Rejlers Abu Dhabi. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
        ''',
        'text_template': '''
Password Reset Request
RADAI - Powered by Rejlers Abu Dhabi

Hello {{first_name}} {{last_name}},

We received a request to reset your password for your RADAI account.

Reset your password here: {{reset_url}}

⚠️ SECURITY NOTICE:
===================
- This password reset link will expire in {{expiry_hours}} hours.
- If you didn't request this password reset, please ignore this email.
- Never share this link with anyone.

FOR SECURITY:
=============
- Choose a strong, unique password
- Use a combination of letters, numbers, and special characters
- Make it at least 8 characters long

If you have any questions or concerns, please contact our support team immediately.

Best regards,
RADAI Team
Powered by Rejlers Abu Dhabi

---
This is an automated message. Please do not reply to this email.
© 2025 RADAI - Rejlers Abu Dhabi
        '''
    },
    'password_reset_required': {
        'subject': 'Password Reset Required - First Login',
        'html_template': '''
<!DOCTYPE html>
<html>
<head>
    <style>
        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background-color: #4F46E5; color: white; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }}
        .content {{ background-color: #f9fafb; padding: 30px; border: 1px solid #e5e7eb; }}
        .button {{ display: inline-block; padding: 12px 30px; background-color: #4F46E5; color: white; text-decoration: none; border-radius: 6px; margin: 20px 0; }}
        .footer {{ text-align: center; padding: 20px; color: #6b7280; font-size: 12px; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Password Reset Required</h1>
        </div>
        <div class="content">
            <h2>Hello {first_name},</h2>
            <p>You are required to reset your password before accessing your account.</p>
            <a href="{reset_url}" class="button">Reset Password Now</a>
            <p>If the button doesn't work, copy and paste this link into your browser:</p>
            <p>{reset_url}</p>
        </div>
        <div class="footer">
            <p>&copy; 2025 RADAI - Rejlers Abu Dhabi. All rights reserved.</p>
        </div>
    </div>
</body>
</html>
        ''',
        'text_template': '''
Password Reset Required

Hello {first_name},

You are required to reset your password before accessing your account.

Reset your password here: {reset_url}

Best regards,
RADAI Team
Powered by Rejlers Abu Dhabi
        '''
    }
}


def get_email_template(template_name, context):
    """
    Get formatted email template with context
    
    Args:
        template_name (str): Name of the template
        context (dict): Context data for template
        
    Returns:
        dict: Dictionary with subject, html_body, and text_body
    """
    if template_name not in EMAIL_TEMPLATES:
        raise ValueError(f"Template '{template_name}' not found")
    
    template = EMAIL_TEMPLATES[template_name]
    
    return {
        'subject': template['subject'].format(**context),
        'html_body': template['html_template'].format(**context),
        'text_body': template['text_template'].format(**context)
    }

