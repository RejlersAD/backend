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
© 2025 RADAI - Rejlers Abu Dhabitomated message. Please do not reply to this email.
© 2025 AIFlow. All rights reserved.
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
            <p>&copy; 2025 AIFlow. All rights reserved.</p>
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
The AIFlow Team
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
