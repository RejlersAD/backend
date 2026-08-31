"""
Enquiry API Views - REJLERS RADAI
Handles customer enquiry submissions and email notifications

Features:
- Form validation
- Email notification to sales team
- Soft-coded email configuration
- Rate limiting
- Error handling
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.conf import settings
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.db.models import Case, Count, F, IntegerField, Q, When
from django.db.models.functions import TruncMonth
from django.contrib.auth import get_user_model
import logging

from apps.core.models import Enquiry, EnquiryActivity, EnquiryFeedback, EnquiryMessage, EnquiryRoutingRule
from apps.core.enquiry_workflow import (
    DEFAULT_ROUTING, add_initial_message, add_response, confirm_resolution, escalate_enquiry,
    normalize_inquiry_type, propose_resolution, route_enquiry, submit_feedback,
)
from apps.core.config.enquiry_access_config import user_has_enquiry_access
from apps.rbac.permissions import HasModuleAccess

logger = logging.getLogger(__name__)


# Soft-coded Configuration
# RBAC module code for enquiry management access (defined in rbac_config.py)
ENQUIRY_MODULE_CODE = 'enquiry_management'
ENQUIRY_CONFIG = {
    'recipient_email': 'tanzeem.agra@rejlers.ae',
    'cc_emails': [],  # Add CC recipients here if needed
    'from_email': settings.DEFAULT_FROM_EMAIL,
    'subject_prefix': '[RADAI Enquiry]',
    'auto_reply': True,
    'save_to_database': True  # Future: save enquiries to database
}

# Services for which the `phone` field is not required. Password reset
# requests originate from /forgot-password where the user typically only
# knows their email.
ENQUIRY_SERVICES_WITHOUT_PHONE = {'password-reset', 'it_request'}

# Notification config — which services generate an admin notification, and
# which notification template to use. Extend when new critical services
# need admin attention.
ENQUIRY_NOTIFICATION_TEMPLATE = {
    'password-reset': 'ENQUIRY_PASSWORD_RESET_REQUEST',
}


def _notify_admins_of_enquiry(enquiry_obj, service, user_email, user_name):
    """
    Send an in-app notification to all admin users when a critical enquiry
    (e.g. password-reset) arrives. Silent no-op for services not listed in
    ENQUIRY_NOTIFICATION_TEMPLATE.
    """
    template_key = ENQUIRY_NOTIFICATION_TEMPLATE.get(service)
    if not template_key or not enquiry_obj:
        return

    from django.contrib.auth import get_user_model
    from apps.notifications.services import NotificationService

    User = get_user_model()
    admins = User.objects.filter(
        is_active=True,
    ).filter(
        # Django admins OR RBAC admin/super_admin roles
        # We use is_staff/is_superuser as the widest safe net; role-based
        # filtering happens in the RBAC layer for finer control if needed.
        Q(is_superuser=True) | Q(is_staff=True)
    ).distinct()

    NotificationService.bulk_notify(
        recipients=admins,
        template_key=template_key,
        user_email=user_email,
        user_name=user_name,
        action_url=f'/admin/enquiries/{enquiry_obj.pk}',
        metadata={
            'enquiry_id': enquiry_obj.pk,
            'service':    service,
            'user_email': user_email,
        },
    )


@api_view(['POST'])
@permission_classes([AllowAny])  # Public endpoint - no authentication required
def submit_enquiry(request):
    """
    Submit customer enquiry and send email notification
    
    Request Body:
    {
        "name": "John Doe",
        "email": "john@example.com",
        "phone": "+971 50 123 4567",
        "company": "ABC Company",
        "subject": "Enquiry about services",
        "message": "I would like to know more about...",
        "service": "pid-analysis",
        "urgency": "normal"
    }
    """
    try:
        # Extract form data
        data = request.data
        name = data.get('name', '').strip()
        email = data.get('email', '').strip()
        phone = data.get('phone', '').strip()
        company = data.get('company', '').strip()
        subject = data.get('subject', '').strip()
        message = data.get('message', '').strip()
        service = data.get('service', '').strip()
        inquiry_type = normalize_inquiry_type(data.get('inquiry_type') or service)
        urgency = data.get('urgency', 'normal').strip()
        
        # Validation
        errors = {}
        if not name:
            errors['name'] = 'Name is required'
        if not email:
            errors['email'] = 'Email is required'
        elif '@' not in email or '.' not in email:
            errors['email'] = 'Invalid email format'
        if not phone and service not in ENQUIRY_SERVICES_WITHOUT_PHONE:
            errors['phone'] = 'Phone number is required'
        if not subject:
            errors['subject'] = 'Subject is required'
        if not message:
            errors['message'] = 'Message is required'
        elif len(message) < 10:
            errors['message'] = 'Message must be at least 10 characters'
        
        if errors:
            return Response({
                'success': False,
                'errors': errors,
                'message': 'Validation failed'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Persist to database (gated by soft-coded flag)
        enquiry_obj = None
        if ENQUIRY_CONFIG.get('save_to_database', True):
            try:
                enquiry_obj = Enquiry.objects.create(
                    name=name,
                    email=email,
                    phone=phone,
                    company=company,
                    subject=subject,
                    message=message,
                    service=service,
                    inquiry_type=inquiry_type,
                    urgency=urgency if urgency in dict(Enquiry.URGENCY_CHOICES) else 'normal',
                    requester=request.user if request.user.is_authenticated else None,
                    source_ip=(request.META.get('HTTP_X_FORWARDED_FOR') or request.META.get('REMOTE_ADDR') or '').split(',')[0].strip() or None,
                    user_agent=(request.META.get('HTTP_USER_AGENT') or '')[:400],
                )
                add_initial_message(enquiry_obj)
                route_enquiry(enquiry_obj)
            except Exception as db_err:
                logger.error(f'Enquiry DB persist failed (continuing with email): {db_err}')

        # Notify administrators via in-app notification (soft-coded per service)
        # For password-reset requests this is critical since SMTP may be down;
        # the notification bell alerts admins so they can action from 9.6 Enquiry.
        try:
            _notify_admins_of_enquiry(enquiry_obj, service, email, name)
        except Exception as notif_err:
            logger.error(f'Enquiry admin notification failed (continuing): {notif_err}')

        # Service name mapping
        service_names = {
            'general': 'General Inquiry',
            'technical_support': 'Technical Support',
            'complaint': 'Complaint',
            'suggestion': 'Suggestion',
            'partnership': 'Partnership',
            'legal': 'Legal',
            'hr': 'HR',
            'it_request': 'IT Request',
            'finance_request': 'Finance Request',
            'procurement': 'Procurement',
            'facility_request': 'Facility Request',
            'other': 'Other',
            # Kept for the dedicated legacy password reset page.
            'password-reset': 'Password Reset Request',
        }
        service_label = service_names.get(service, 'General Enquiry')
        
        # Urgency level mapping
        urgency_labels = {
            'low': '⏰ Low Priority',
            'normal': '📅 Normal Priority',
            'high': '⚡ High Priority',
            'urgent': '🚨 URGENT'
        }
        urgency_label = urgency_labels.get(urgency, 'Normal Priority')
        
        # Prepare email content
        email_subject = f"{ENQUIRY_CONFIG['subject_prefix']} {subject}"
        
        # HTML Email Template
        html_content = f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    line-height: 1.6;
                    color: #333;
                }}
                .container {{
                    max-width: 600px;
                    margin: 0 auto;
                    padding: 20px;
                    background-color: #f9f9f9;
                }}
                .header {{
                    background: linear-gradient(135deg, #1e40af 0%, #3b82f6 100%);
                    color: white;
                    padding: 30px;
                    border-radius: 10px 10px 0 0;
                    text-align: center;
                }}
                .content {{
                    background: white;
                    padding: 30px;
                    border-radius: 0 0 10px 10px;
                    box-shadow: 0 2px 4px rgba(0,0,0,0.1);
                }}
                .field {{
                    margin-bottom: 20px;
                    padding: 15px;
                    background-color: #f8f9fa;
                    border-left: 4px solid #3b82f6;
                    border-radius: 4px;
                }}
                .label {{
                    font-weight: bold;
                    color: #1e40af;
                    margin-bottom: 5px;
                }}
                .value {{
                    color: #333;
                }}
                .message-box {{
                    background-color: #e3f2fd;
                    padding: 20px;
                    border-radius: 8px;
                    margin-top: 20px;
                    border: 2px solid #3b82f6;
                }}
                .footer {{
                    margin-top: 30px;
                    padding: 20px;
                    text-align: center;
                    color: #666;
                    font-size: 12px;
                    border-top: 1px solid #ddd;
                }}
                .urgency {{
                    display: inline-block;
                    padding: 8px 16px;
                    border-radius: 20px;
                    font-weight: bold;
                }}
                .urgency-urgent {{
                    background-color: #fee2e2;
                    color: #dc2626;
                }}
                .urgency-high {{
                    background-color: #fef3c7;
                    color: #d97706;
                }}
                .urgency-normal {{
                    background-color: #dbeafe;
                    color: #2563eb;
                }}
                .urgency-low {{
                    background-color: #f3f4f6;
                    color: #6b7280;
                }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1 style="margin: 0;">📧 New Enquiry Received</h1>
                    <p style="margin: 10px 0 0 0;">RADAI Customer Enquiry System</p>
                </div>
                
                <div class="content">
                    <div class="field">
                        <div class="label">Urgency Level:</div>
                        <div class="value">
                            <span class="urgency urgency-{urgency}">{urgency_label}</span>
                        </div>
                    </div>
                    
                    <div class="field">
                        <div class="label">📝 Subject:</div>
                        <div class="value">{subject}</div>
                    </div>
                    
                    <div class="field">
                        <div class="label">👤 Customer Name:</div>
                        <div class="value">{name}</div>
                    </div>
                    
                    <div class="field">
                        <div class="label">📧 Email:</div>
                        <div class="value"><a href="mailto:{email}">{email}</a></div>
                    </div>
                    
                    <div class="field">
                        <div class="label">📞 Phone:</div>
                        <div class="value">{phone}</div>
                    </div>
                    
                    {f'<div class="field"><div class="label">🏢 Company:</div><div class="value">{company}</div></div>' if company else ''}
                    
                    <div class="field">
                        <div class="label">🔧 Service of Interest:</div>
                        <div class="value">{service_label}</div>
                    </div>
                    
                    <div class="message-box">
                        <div class="label">💬 Message:</div>
                        <div class="value" style="white-space: pre-wrap; margin-top: 10px;">{message}</div>
                    </div>
                    
                    <div class="footer">
                        <p><strong>Submitted:</strong> {timezone.now().strftime('%B %d, %Y at %I:%M %p UTC')}</p>
                        <p>This is an automated message from RADAI Enquiry System</p>
                        <p>Reply directly to this email to respond to the customer</p>
                        <hr style="margin: 20px 0; border: none; border-top: 1px solid #ddd;">
                        <p>© 2025 REJLERS AB • Engineering Excellence Since 1942</p>
                        <p><a href="https://www.radai.ae">www.radai.ae</a> | <a href="https://www.rejlers.com">www.rejlers.com</a></p>
                    </div>
                </div>
            </div>
        </body>
        </html>
        """
        
        # Plain text version
        text_content = f"""
        New Enquiry Received - RADAI
        =============================
        
        Urgency: {urgency_label}
        Subject: {subject}
        
        Customer Details:
        -----------------
        Name: {name}
        Email: {email}
        Phone: {phone}
        {f'Company: {company}' if company else ''}
        
        Service Interest: {service_label}
        
        Message:
        --------
        {message}
        
        Submitted: {timezone.now().strftime('%B %d, %Y at %I:%M %p UTC')}
        
        ---
        This is an automated message from RADAI Enquiry System
        Reply directly to this email to respond to the customer
        """
        
        # Send email
        email_message = EmailMultiAlternatives(
            subject=email_subject,
            body=text_content,
            from_email=ENQUIRY_CONFIG['from_email'],
            to=[ENQUIRY_CONFIG['recipient_email']],
            cc=ENQUIRY_CONFIG['cc_emails'],
            reply_to=[email]  # Set customer's email as reply-to
        )
        email_message.attach_alternative(html_content, "text/html")
        try:
            email_message.send(fail_silently=False)
            logger.info(f"✅ Enquiry submitted: {subject} from {email}")
        except Exception as mail_err:
            # Email failure must not lose the enquiry — it is already persisted.
            logger.error(f"⚠️ Enquiry email failed (DB row was saved): {mail_err}")
        
        # Optional: Send auto-reply to customer
        if ENQUIRY_CONFIG['auto_reply']:
            try:
                auto_reply_subject = "Thank you for contacting REJLERS RADAI"
                auto_reply_html = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <style>
                        body {{ font-family: Arial, sans-serif; line-height: 1.6; color: #333; }}
                        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
                        .header {{ background: linear-gradient(135deg, #1e40af 0%, #3b82f6 100%); color: white; padding: 30px; border-radius: 10px; text-align: center; }}
                        .content {{ background: white; padding: 30px; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1 style="margin: 0;">Thank You!</h1>
                        </div>
                        <div class="content">
                            <p>Dear {name},</p>
                            <p>Thank you for contacting REJLERS RADAI. We have received your enquiry and our team will review it shortly.</p>
                            <p><strong>Your Enquiry Details:</strong></p>
                            <ul>
                                <li><strong>Subject:</strong> {subject}</li>
                                <li><strong>Service:</strong> {service_label}</li>
                                <li><strong>Reference:</strong> {timezone.now().strftime('%Y%m%d-%H%M%S')}</li>
                            </ul>
                            <p>We aim to respond to all enquiries within 24 hours. For urgent matters, please call us at <strong>+971 2 639 7449</strong>.</p>
                            <p>Best regards,<br><strong>REJLERS RADAI Team</strong></p>
                            <hr style="margin: 20px 0; border: none; border-top: 1px solid #ddd;">
                            <p style="font-size: 12px; color: #666;">© 2025 REJLERS AB | <a href="https://www.radai.ae">www.radai.ae</a></p>
                        </div>
                    </div>
                </body>
                </html>
                """
                
                auto_reply = EmailMultiAlternatives(
                    subject=auto_reply_subject,
                    body=f"Dear {name},\n\nThank you for contacting REJLERS RADAI. We have received your enquiry and will respond within 24 hours.\n\nBest regards,\nRADAI Team",
                    from_email=ENQUIRY_CONFIG['from_email'],
                    to=[email]
                )
                auto_reply.attach_alternative(auto_reply_html, "text/html")
                auto_reply.send(fail_silently=True)
                logger.info(f"✅ Auto-reply sent to: {email}")
            except Exception as e:
                logger.warning(f"⚠️ Auto-reply failed: {str(e)}")
        
        return Response({
            'success': True,
            'message': 'Your enquiry has been submitted successfully. We will get back to you within 24 hours.',
            'reference': (f'ENQ-{enquiry_obj.id:06d}' if enquiry_obj else timezone.now().strftime('%Y%m%d-%H%M%S'))
        }, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.error(f"❌ Enquiry submission error: {str(e)}")
        return Response({
            'success': False,
            'message': 'Failed to submit enquiry. Please try again or contact us directly at tanzeem.agra@rejlers.ae',
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ============================================================================
# ADMIN ENDPOINTS — list / detail / update / delete / stats
# Used by the 9.6 Enquiry admin page (frontend/src/pages/Admin/EnquiryManagement.jsx)
# ============================================================================

def _user_summary(user):
    if not user:
        return None
    return {'id': user.pk, 'name': user.get_full_name() or user.username, 'email': user.email}


def _serialize_enquiry(e: Enquiry, *, detail=False, include_internal=False) -> dict:
    payload = {
        'id':          e.id,
        'reference':   f'ENQ-{e.id:06d}',
        'name':        e.name,
        'email':       e.email,
        'phone':       e.phone,
        'company':     e.company,
        'subject':     e.subject,
        'message':     e.message,
        'service':     e.service,
        'inquiry_type': e.inquiry_type,
        'inquiry_type_label': e.get_inquiry_type_display(),
        'department': e.department,
        'urgency':     e.urgency,
        'status':      e.status,
        'admin_notes': e.admin_notes,
        'channel': e.channel,
        'escalation_level': e.escalation_level,
        'escalated_at': e.escalated_at.isoformat() if e.escalated_at else None,
        'escalation_reason': e.escalation_reason,
        'resolution_summary': e.resolution_summary,
        'resolution_proposed_at': e.resolution_proposed_at.isoformat() if e.resolution_proposed_at else None,
        'resolution_confirmed_at': e.resolution_confirmed_at.isoformat() if e.resolution_confirmed_at else None,
        'approval_required': e.approval_required,
        'approval_status': e.approval_status,
        'approved_by': _user_summary(e.approved_by),
        'approved_at': e.approved_at.isoformat() if e.approved_at else None,
        'requester': _user_summary(e.requester),
        'assigned_to': _user_summary(e.assigned_to),
        'assigned_at': e.assigned_at.isoformat() if e.assigned_at else None,
        'due_at': e.due_at.isoformat() if e.due_at else None,
        'first_response_at': e.first_response_at.isoformat() if e.first_response_at else None,
        'resolved_at': e.resolved_at.isoformat() if e.resolved_at else None,
        'closed_at': e.closed_at.isoformat() if e.closed_at else None,
        'is_overdue': bool(e.due_at and e.due_at < timezone.now() and e.status not in ('resolved', 'closed', 'spam', 'pending_confirmation')),
        'source_ip':   e.source_ip,
        'user_agent':  e.user_agent,
        'created_at':  e.created_at.isoformat() if e.created_at else None,
        'updated_at':  e.updated_at.isoformat() if e.updated_at else None,
    }
    if detail:
        try:
            feedback = e.feedback
        except EnquiryFeedback.DoesNotExist:
            feedback = None
        payload['feedback'] = ({
            'rating': feedback.rating, 'comment': feedback.comment,
            'resolution_confirmed': feedback.resolution_confirmed,
            'would_recommend': feedback.would_recommend,
            'submitted_by': _user_summary(feedback.submitted_by),
            'created_at': feedback.created_at.isoformat(),
        } if feedback else None)
        messages = e.messages.select_related('author').all()
        if not include_internal:
            messages = messages.filter(is_internal=False)
        payload['messages'] = [
            {
                'id': message.pk, 'body': message.body, 'sender_type': message.sender_type,
                'is_internal': message.is_internal, 'author': _user_summary(message.author),
                'created_at': message.created_at.isoformat(),
            }
            for message in messages
        ]
        if include_internal:
            payload['activities'] = [
                {'id': activity.pk, 'action': activity.action, 'actor': _user_summary(activity.actor),
                 'details': activity.details, 'created_at': activity.created_at.isoformat()}
                for activity in e.activities.select_related('actor').all()
            ]
    return payload


def _can_manage_enquiries(user):
    return user_has_enquiry_access(user)


def _managed_queryset(user):
    queryset = Enquiry.objects.select_related('requester', 'assigned_to', 'assigned_by', 'approved_by')
    if _can_manage_enquiries(user):
        return queryset
    return queryset.filter(assigned_to=user)


def _requester_queryset(user):
    return Enquiry.objects.select_related('requester', 'assigned_to', 'approved_by').filter(
        Q(requester=user) | Q(requester__isnull=True, email__iexact=user.email)
    ).distinct()


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_enquiries(request):
    """List enquiries with optional filters: ?status=&urgency=&service=&search=&page=&page_size="""  
    qs = _managed_queryset(request.user)
    if not _can_manage_enquiries(request.user) and not qs.exists():
        return Response({'detail': 'You are not an enquiry representative.'}, status=status.HTTP_403_FORBIDDEN)

    f_status  = request.query_params.get('status')
    f_urgency = request.query_params.get('urgency')
    f_service = request.query_params.get('service')
    f_type = request.query_params.get('inquiry_type')
    f_department = request.query_params.get('department')
    f_assignee = request.query_params.get('assigned_to')
    f_search  = request.query_params.get('search', '').strip()

    if f_status:
        qs = qs.filter(status=f_status)
    if f_urgency:
        qs = qs.filter(urgency=f_urgency)
    if f_service:
        qs = qs.filter(service=f_service)
    if f_type:
        qs = qs.filter(inquiry_type=f_type)
    if f_department:
        qs = qs.filter(department=f_department)
    if f_assignee == 'me':
        qs = qs.filter(assigned_to=request.user)
    elif f_assignee == 'unassigned':
        qs = qs.filter(assigned_to__isnull=True)
    if f_search:
        qs = qs.filter(
            Q(name__icontains=f_search) |
            Q(email__icontains=f_search) |
            Q(company__icontains=f_search) |
            Q(subject__icontains=f_search) |
            Q(message__icontains=f_search)
        )

    try:
        page      = max(1, int(request.query_params.get('page', 1)))
        page_size = min(100, max(1, int(request.query_params.get('page_size', 25))))
    except (TypeError, ValueError):
        page, page_size = 1, 25

    total = qs.count()
    start = (page - 1) * page_size
    items = [_serialize_enquiry(e) for e in qs[start:start + page_size]]

    return Response({
        'success':   True,
        'count':     total,
        'page':      page,
        'page_size': page_size,
        'results':   items,
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def enquiry_stats(request):
    """Aggregate counts for dashboard widgets at the top of the admin page."""
    enquiries = _managed_queryset(request.user)
    if not _can_manage_enquiries(request.user) and not enquiries.exists():
        return Response({'detail': 'You are not an enquiry representative.'}, status=status.HTTP_403_FORBIDDEN)
    by_status_qs = enquiries.values('status').annotate(c=Count('id'))
    by_urgency_qs = enquiries.values('urgency').annotate(c=Count('id'))
    by_department_qs = enquiries.values('department').annotate(c=Count('id')).order_by('-c')
    by_type_qs = enquiries.values('inquiry_type').annotate(c=Count('id')).order_by('-c')
    monthly_qs = (
        enquiries.annotate(month=TruncMonth('created_at'))
        .values('month').annotate(c=Count('id')).order_by('month')
    )
    completed = enquiries.filter(status__in=['resolved', 'closed'])
    completed_with_sla = completed.filter(due_at__isnull=False)
    sla_total = completed_with_sla.count()
    sla_met = completed_with_sla.filter(
        Q(resolved_at__isnull=False, resolved_at__lte=F('due_at')) |
        Q(resolved_at__isnull=True, closed_at__lte=F('due_at'))
    ).count()
    response_durations = [
        (first_response - created).total_seconds() / 3600
        for created, first_response in enquiries.filter(first_response_at__isnull=False)
        .values_list('created_at', 'first_response_at')
    ]
    priority_order = Case(
        When(urgency='urgent', then=0), When(urgency='high', then=1),
        When(urgency='normal', then=2), default=3, output_field=IntegerField(),
    )
    active = enquiries.exclude(status__in=['resolved', 'closed', 'spam'])
    priority_items = active.annotate(priority_order=priority_order).order_by(
        'priority_order', 'due_at', '-created_at',
    )[:8]
    deadlines = active.filter(due_at__isnull=False).order_by('due_at')[:5]
    return Response({
        'success':   True,
        'total':     enquiries.count(),
        'new':       enquiries.filter(status='new').count(),
        'assigned_to_me': enquiries.filter(assigned_to=request.user).exclude(status__in=['resolved', 'closed', 'spam']).count(),
        'unassigned': enquiries.filter(assigned_to__isnull=True).count(),
        'overdue': enquiries.filter(due_at__lt=timezone.now()).exclude(status__in=['resolved', 'closed', 'spam', 'pending_confirmation']).count(),
        'by_status': {row['status']: row['c'] for row in by_status_qs},
        'by_urgency':{row['urgency']: row['c'] for row in by_urgency_qs},
        'by_department': [{'name': row['department'] or 'Unrouted', 'count': row['c']} for row in by_department_qs],
        'by_type': [{'name': row['inquiry_type'], 'count': row['c']} for row in by_type_qs],
        'monthly_trend': [
            {'month': row['month'].strftime('%b %Y'), 'count': row['c']}
            for row in monthly_qs if row['month']
        ][-6:],
        'sla_compliance': round((sla_met / sla_total) * 100, 1) if sla_total else 100.0,
        'average_response_hours': round(sum(response_durations) / len(response_durations), 1) if response_durations else 0,
        'funnel': {
            'intake': enquiries.count(),
            'assigned': enquiries.exclude(status='new').exclude(assigned_to__isnull=True).count(),
            'in_review': enquiries.filter(status__in=['in_progress', 'waiting_user', 'responded']).count(),
            'resolution': completed.count(),
        },
        'priority_items': [_serialize_enquiry(enquiry) for enquiry in priority_items],
        'deadlines': [_serialize_enquiry(enquiry) for enquiry in deadlines],
    })

@api_view(['GET', 'PATCH', 'DELETE'])
@permission_classes([IsAuthenticated])
def enquiry_detail(request, pk: int):
    """Retrieve, update (status / admin_notes), or delete a single enquiry."""
    enquiry = get_object_or_404(_managed_queryset(request.user), pk=pk)

    if request.method == 'GET':
        return Response({'success': True, 'enquiry': _serialize_enquiry(enquiry, detail=True, include_internal=True)})

    if request.method == 'DELETE':
        if not _can_manage_enquiries(request.user):
            return Response({'detail': 'Only enquiry administrators can delete requests.'}, status=status.HTTP_403_FORBIDDEN)
        enquiry.delete()
        return Response({'success': True, 'message': 'Enquiry deleted'})

    # PATCH — only allow safe admin-controlled fields
    allowed = {'status', 'admin_notes', 'urgency', 'department', 'assigned_to', 'approval_status'}
    payload = {k: v for k, v in (request.data or {}).items() if k in allowed}

    if 'status' in payload and payload['status'] not in dict(Enquiry.STATUS_CHOICES):
        return Response({'success': False, 'message': 'Invalid status'}, status=status.HTTP_400_BAD_REQUEST)
    if 'urgency' in payload and payload['urgency'] not in dict(Enquiry.URGENCY_CHOICES):
        return Response({'success': False, 'message': 'Invalid urgency'}, status=status.HTTP_400_BAD_REQUEST)
    if payload.get('status') in ('resolved', 'closed'):
        return Response(
            {'success': False, 'message': 'Use the resolution workflow so requester confirmation and feedback are recorded.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if 'approval_status' in payload:
        if not _can_manage_enquiries(request.user):
            return Response({'detail': 'Manager approval access is required.'}, status=status.HTTP_403_FORBIDDEN)
        if payload['approval_status'] not in ('pending', 'approved', 'rejected', 'not_required'):
            return Response({'detail': 'Invalid approval status.'}, status=status.HTTP_400_BAD_REQUEST)

    previous = {key: getattr(enquiry, f'{key}_id' if key == 'assigned_to' else key) for key in payload}
    if 'assigned_to' in payload:
        assignee_id = payload.pop('assigned_to')
        assignee = get_object_or_404(get_user_model().objects.filter(is_active=True), pk=assignee_id) if assignee_id else None
        enquiry.assigned_to = assignee
        enquiry.assigned_by = request.user
        enquiry.assigned_at = timezone.now() if assignee else None
    for k, v in payload.items():
        setattr(enquiry, k, v)
    if payload.get('approval_status') == 'approved':
        enquiry.approved_by = request.user
        enquiry.approved_at = timezone.now()
    elif payload.get('approval_status') == 'rejected':
        enquiry.approved_by = request.user
        enquiry.approved_at = timezone.now()
    now = timezone.now()
    if payload.get('status') == 'resolved' and not enquiry.resolved_at:
        enquiry.resolved_at = now
    if payload.get('status') == 'closed' and not enquiry.closed_at:
        enquiry.closed_at = now
    update_fields = list(payload.keys()) + ['updated_at']
    if 'assigned_to' in previous:
        update_fields += ['assigned_to', 'assigned_by', 'assigned_at']
    if 'status' in payload:
        update_fields += ['resolved_at', 'closed_at']
    if 'approval_status' in payload:
        update_fields += ['approved_by', 'approved_at']
    enquiry.save(update_fields=list(dict.fromkeys(update_fields)))
    EnquiryActivity.objects.create(
        enquiry=enquiry, actor=request.user, action='enquiry_updated',
        details={'before': previous, 'after': {key: request.data.get(key) for key in previous}},
    )
    if 'assigned_to' in previous and enquiry.assigned_to:
        from apps.notifications.services import NotificationService
        NotificationService.create_notification(
            enquiry.assigned_to,
            title=f'Request assigned: {enquiry.reference}',
            message=f'{enquiry.get_inquiry_type_display()} assigned to you: {enquiry.subject}',
            category='INFO', priority='HIGH' if enquiry.urgency in ('high', 'urgent') else 'NORMAL',
            action_url=f'/admin/enquiries/{enquiry.pk}', action_label='Open Request',
            metadata={'enquiry_id': enquiry.pk}, sender=request.user,
        )
    if 'approval_status' in previous and enquiry.assigned_to:
        from apps.notifications.services import NotificationService
        NotificationService.create_notification(
            enquiry.assigned_to, sender=request.user,
            title=f'Approval {enquiry.approval_status}: {enquiry.reference}',
            message=f'Handling approval for "{enquiry.subject}" is {enquiry.approval_status}.',
            category='INFO', priority='HIGH', action_url=f'/admin/enquiries/{enquiry.pk}',
            action_label='Open Request', metadata={'enquiry_id': enquiry.pk},
        )
    if 'status' in previous and previous['status'] != enquiry.status and enquiry.requester:
        from apps.notifications.services import NotificationService
        NotificationService.create_notification(
            enquiry.requester, sender=request.user,
            title=f'Request updated: {enquiry.reference}',
            message=f'Your request status changed to {enquiry.get_status_display()}.',
            category='INFO', priority='NORMAL', action_url=f'/my-enquiries/{enquiry.pk}',
            action_label='View Request', metadata={'enquiry_id': enquiry.pk},
        )
    if payload.get('status') in ('resolved', 'closed') and enquiry.requester:
        from apps.notifications.services import NotificationService
        NotificationService.create_notification(
            enquiry.requester, sender=request.user,
            title=f'Request {payload["status"]}: {enquiry.reference}',
            message=f'Your request "{enquiry.subject}" was marked {payload["status"]}.',
            category='INFO', priority='NORMAL', action_url=f'/my-enquiries/{enquiry.pk}',
            action_label='View Request', metadata={'enquiry_id': enquiry.pk},
        )

    return Response({'success': True, 'enquiry': _serialize_enquiry(enquiry, detail=True, include_internal=True)})

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def enquiry_response(request, pk: int):
    enquiry = get_object_or_404(_managed_queryset(request.user), pk=pk)
    try:
        add_response(
            enquiry, actor=request.user, body=request.data.get('body'),
            is_internal=bool(request.data.get('is_internal', False)),
        )
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    enquiry.refresh_from_db()
    return Response({'success': True, 'enquiry': _serialize_enquiry(enquiry, detail=True, include_internal=True)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def enquiry_escalate(request, pk: int):
    enquiry = get_object_or_404(_managed_queryset(request.user), pk=pk)
    escalate_enquiry(enquiry, actor=request.user, reason=request.data.get('reason'))
    enquiry.refresh_from_db()
    return Response({'success': True, 'enquiry': _serialize_enquiry(enquiry, detail=True, include_internal=True)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def enquiry_propose_resolution(request, pk: int):
    enquiry = get_object_or_404(_managed_queryset(request.user), pk=pk)
    try:
        propose_resolution(enquiry, actor=request.user, summary=request.data.get('summary'))
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    enquiry.refresh_from_db()
    return Response({'success': True, 'enquiry': _serialize_enquiry(enquiry, detail=True, include_internal=True)})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_enquiries(request):
    queryset = _requester_queryset(request.user)
    return Response({
        'success': True, 'count': queryset.count(),
        'results': [_serialize_enquiry(enquiry) for enquiry in queryset[:250]],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def my_enquiry_detail(request, pk: int):
    enquiry = get_object_or_404(_requester_queryset(request.user), pk=pk)
    return Response({'success': True, 'enquiry': _serialize_enquiry(enquiry, detail=True)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def my_enquiry_response(request, pk: int):
    enquiry = get_object_or_404(_requester_queryset(request.user), pk=pk)
    if enquiry.status in ('closed', 'spam'):
        return Response({'detail': 'This request is closed.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        add_response(enquiry, actor=request.user, body=request.data.get('body'), requester_reply=True)
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    enquiry.refresh_from_db()
    return Response({'success': True, 'enquiry': _serialize_enquiry(enquiry, detail=True)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def my_enquiry_resolution(request, pk: int):
    enquiry = get_object_or_404(_requester_queryset(request.user), pk=pk)
    if enquiry.status != 'pending_confirmation':
        return Response({'detail': 'This request is not awaiting resolution confirmation.'}, status=status.HTTP_400_BAD_REQUEST)
    accepted = request.data.get('accepted') in (True, 'true', '1', 1)
    confirm_resolution(enquiry, actor=request.user, accepted=accepted, comment=request.data.get('comment'))
    enquiry.refresh_from_db()
    return Response({'success': True, 'enquiry': _serialize_enquiry(enquiry, detail=True)})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def my_enquiry_feedback(request, pk: int):
    enquiry = get_object_or_404(_requester_queryset(request.user), pk=pk)
    try:
        submit_feedback(
            enquiry, actor=request.user, rating=request.data.get('rating'),
            comment=request.data.get('comment'), would_recommend=request.data.get('would_recommend'),
        )
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    enquiry.refresh_from_db()
    return Response({'success': True, 'enquiry': _serialize_enquiry(enquiry, detail=True)})


@api_view(['GET', 'POST'])
@permission_classes([AllowAny])
def public_enquiry_feedback(request, token):
    enquiry = get_object_or_404(Enquiry, feedback_token=token)
    if request.method == 'GET':
        return Response({
            'reference': enquiry.reference, 'subject': enquiry.subject,
            'status': enquiry.status, 'resolution_summary': enquiry.resolution_summary,
            'feedback_submitted': hasattr(enquiry, 'feedback'),
        })
    accepted = request.data.get('accepted', True) in (True, 'true', '1', 1)
    if enquiry.status == 'pending_confirmation':
        confirm_resolution(enquiry, accepted=accepted, comment=request.data.get('comment'))
    if not accepted:
        enquiry.refresh_from_db()
        return Response({'success': True, 'status': enquiry.status})
    try:
        submit_feedback(
            enquiry, rating=request.data.get('rating'), comment=request.data.get('comment'),
            would_recommend=request.data.get('would_recommend'),
        )
    except ValueError as exc:
        return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    return Response({'success': True, 'status': 'closed'})


@api_view(['GET'])
@permission_classes([AllowAny])
def enquiry_options(request):
    rules = {rule.inquiry_type: rule for rule in EnquiryRoutingRule.objects.filter(is_active=True)}
    return Response({
        'types': [
            {'value': value, 'label': label,
             'department': (rules[value].department if value in rules else DEFAULT_ROUTING[value][0]),
             'sla_hours': (rules[value].sla_hours if value in rules else DEFAULT_ROUTING[value][1])}
            for value, label in Enquiry.TYPE_CHOICES
        ],
        'statuses': [{'value': value, 'label': label} for value, label in Enquiry.STATUS_CHOICES],
        'urgencies': [{'value': value, 'label': label} for value, label in Enquiry.URGENCY_CHOICES],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def enquiry_representatives(request):
    if not _can_manage_enquiries(request.user):
        return Response({'detail': 'Enquiry management access is required.'}, status=status.HTTP_403_FORBIDDEN)
    from apps.core.config.enquiry_access_config import (
        ENQUIRY_ADMIN_ROLES, ENQUIRY_MODULE_CODE, ENQUIRY_SPECIAL_ACCESS_USERS,
    )
    users = get_user_model().objects.filter(is_active=True).filter(
        Q(is_superuser=True) |
        Q(email__in=ENQUIRY_SPECIAL_ACCESS_USERS) |
        Q(
            rbac_profile__is_deleted=False,
            rbac_profile__status='active',
            rbac_profile__userrole__role__is_active=True,
            rbac_profile__userrole__role__code__in=ENQUIRY_ADMIN_ROLES,
        ) |
        Q(
            rbac_profile__is_deleted=False,
            rbac_profile__status='active',
            rbac_profile__userrole__role__is_active=True,
            rbac_profile__userrole__role__modules__is_active=True,
            rbac_profile__userrole__role__modules__code=ENQUIRY_MODULE_CODE,
        )
    ).select_related('rbac_profile').distinct().order_by('first_name', 'last_name', 'username')
    return Response({'results': [
        {**_user_summary(user), 'department': getattr(getattr(user, 'rbac_profile', None), 'department', '')}
        for user in users
    ]})

