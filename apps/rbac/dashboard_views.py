from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from apps.rbac.models import UserProfile, AuditLog
from apps.users.models import User
from django.db.models import Count, Q
from datetime import datetime, timedelta
import boto3
from django.conf import settings
from botocore.exceptions import ClientError

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_dashboard_stats(request):
    """Get comprehensive dashboard statistics for logged-in user"""
    try:
        user = request.user
        profile = user.rbac_profile
        
        # Get date ranges
        today = datetime.now().date()
        week_ago = today - timedelta(days=7)
        month_ago = today - timedelta(days=30)
        
        # Get user's accessible modules
        user_modules = profile.get_all_modules()
        module_codes = [m.code for m in user_modules]
        
        # Get recent activity from audit logs
        recent_activities = AuditLog.objects.filter(
            user=user
        ).order_by('-timestamp')[:10].values(
            'action', 'resource_type', 'resource_repr', 'timestamp', 'ip_address'
        )
        
        # Activity statistics
        total_activities = AuditLog.objects.filter(user=user).count()
        activities_this_week = AuditLog.objects.filter(
            user=user, 
            timestamp__gte=week_ago
        ).count()
        activities_this_month = AuditLog.objects.filter(
            user=user,
            timestamp__gte=month_ago
        ).count()
        
        # Get S3 user files count
        s3_files_count = 0
        s3_total_size = 0
        try:
            # Check if S3 is configured
            if not hasattr(settings, 'AWS_STORAGE_BUCKET_NAME') or not settings.AWS_STORAGE_BUCKET_NAME:
                # S3 not configured, skip
                pass
            else:
                s3_client = boto3.client(
                    's3',
                    aws_access_key_id=getattr(settings, 'AWS_ACCESS_KEY_ID', None),
                    aws_secret_access_key=getattr(settings, 'AWS_SECRET_ACCESS_KEY', None),
                    region_name=getattr(settings, 'AWS_S3_REGION_NAME', 'us-east-1')
                )
                
                # User folder path
                user_folder = f"users/{user.email}/"
                
                response = s3_client.list_objects_v2(
                    Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                    Prefix=user_folder
                )
                
                if 'Contents' in response:
                    s3_files_count = len(response['Contents'])
                    s3_total_size = sum(obj['Size'] for obj in response['Contents'])
        except Exception as e:
            print(f"Error fetching S3 files: {e}")
        
        # Module-specific stats (can be extended based on modules)
        module_stats = {}
        
        # PFD specific stats if user has access
        if 'PFD' in module_codes:
            from apps.pfd_converter.models import PFDDocument
            pfd_documents = PFDDocument.objects.filter(uploaded_by=user).count()
            module_stats['PFD'] = {
                'documents_processed': pfd_documents,
                'icon': '📄',
                'color': 'blue'
            }
        
        # PID specific stats if user has access
        if 'PID' in module_codes:
            # You can add PID specific stats here
            module_stats['PID'] = {
                'documents_analyzed': 0,  # Update with actual count
                'icon': '🔍',
                'color': 'green'
            }
        
        # CRS specific stats if user has access
        if 'CRS' in module_codes:
            module_stats['CRS'] = {
                'documents_managed': 0,  # Update with actual count
                'icon': '📋',
                'color': 'yellow'
            }
        
        # Project Control stats if user has access
        if 'PROJECT_CONTROL' in module_codes:
            module_stats['PROJECT_CONTROL'] = {
                'projects_managed': 0,  # Update with actual count
                'icon': '📊',
                'color': 'purple'
            }
        
        return Response({
            'user': {
                'email': user.email,
                'full_name': f"{user.first_name} {user.last_name}",
                'department': profile.department,
                'job_title': profile.job_title,
                'organization': profile.organization.name,
                'status': profile.status,
                'member_since': profile.created_at.strftime('%B %Y')
            },
            'accessible_modules': [
                {
                    'code': m.code,
                    'name': m.name,
                    'is_active': m.is_active
                } for m in user_modules
            ],
            'activity_stats': {
                'total': total_activities,
                'this_week': activities_this_week,
                'this_month': activities_this_month,
                'recent_activities': list(recent_activities)
            },
            'storage_stats': {
                'files_count': s3_files_count,
                'total_size_bytes': s3_total_size,
                'total_size_mb': round(s3_total_size / (1024 * 1024), 2)
            },
            'module_stats': module_stats
        })
        
    except Exception as e:
        return Response({
            'error': str(e),
            'message': 'Failed to fetch dashboard statistics'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_files_list(request):
    """List all files for the logged-in user from S3"""
    try:
        user = request.user
        
        # Check if S3 is configured
        if not hasattr(settings, 'AWS_STORAGE_BUCKET_NAME') or not settings.AWS_STORAGE_BUCKET_NAME:
            return Response({
                'files': [],
                'count': 0,
                'user_folder': f"users/{user.email}/",
                'message': 'S3 storage not configured'
            })
        
        s3_client = boto3.client(
            's3',
            aws_access_key_id=getattr(settings, 'AWS_ACCESS_KEY_ID', None),
            aws_secret_access_key=getattr(settings, 'AWS_SECRET_ACCESS_KEY', None),
            region_name=getattr(settings, 'AWS_S3_REGION_NAME', 'us-east-1')
        )
        
        # User folder path
        user_folder = f"users/{user.email}/"
        
        response = s3_client.list_objects_v2(
            Bucket=settings.AWS_STORAGE_BUCKET_NAME,
            Prefix=user_folder
        )
        
        files = []
        if 'Contents' in response:
            for obj in response['Contents']:
                # Skip the folder itself
                if obj['Key'] == user_folder:
                    continue
                    
                files.append({
                    'key': obj['Key'],
                    'filename': obj['Key'].replace(user_folder, ''),
                    'size': obj['Size'],
                    'size_mb': round(obj['Size'] / (1024 * 1024), 2),
                    'last_modified': obj['LastModified'].isoformat(),
                    'download_url': s3_client.generate_presigned_url(
                        'get_object',
                        Params={
                            'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                            'Key': obj['Key']
                        },
                        ExpiresIn=3600  # 1 hour
                    )
                })
        
        return Response({
            'files': files,
            'count': len(files),
            'user_folder': user_folder
        })
        
    except Exception as e:
        return Response({
            'error': str(e),
            'message': 'Failed to fetch user files'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_activity_timeline(request):
    """Get detailed activity timeline for the user"""
    try:
        user = request.user
        days = int(request.GET.get('days', 30))
        
        start_date = datetime.now() - timedelta(days=days)
        
        activities = AuditLog.objects.filter(
            user=user,
            timestamp__gte=start_date
        ).order_by('-timestamp').values(
            'id', 'action', 'resource_type', 'resource_repr', 
            'timestamp', 'ip_address', 'metadata'
        )[:100]  # Limit to 100 recent activities
        
        # Group by date
        timeline = {}
        for activity in activities:
            date_key = activity['timestamp'].strftime('%Y-%m-%d')
            if date_key not in timeline:
                timeline[date_key] = []
            timeline[date_key].append({
                'id': activity['id'],
                'action': activity['action'],
                'resource_type': activity['resource_type'],
                'resource': activity['resource_repr'],
                'time': activity['timestamp'].strftime('%H:%M:%S'),
                'ip': activity['ip_address'],
                'metadata': activity['metadata']
            })
        
        return Response({
            'timeline': timeline,
            'total_activities': len(activities),
            'date_range': f"{days} days"
        })
        
    except Exception as e:
        return Response({
            'error': str(e),
            'message': 'Failed to fetch activity timeline'
        }, status=500)
