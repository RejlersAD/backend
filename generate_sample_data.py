"""
Generate Sample AI Analytics Data
Creates realistic test data for the AI-powered admin dashboard
"""
import os
import django
import random
from datetime import datetime, timedelta
from decimal import Decimal

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.rbac.analytics_models import (
    SystemMetrics, UserActivityAnalytics, SecurityAlert,
    PredictiveInsight, FeatureUsageAnalytics, ErrorLogAnalytics,
    SystemHealthCheck
)
from apps.users.models import User
from django.utils import timezone


def create_system_metrics(days=7):
    """Create system performance metrics"""
    print("Creating system metrics...")
    now = timezone.now()
    
    for day in range(days):
        for hour in range(24):
            timestamp = now - timedelta(days=day, hours=hour)
            SystemMetrics.objects.create(
                timestamp=timestamp,
                avg_response_time_ms=random.uniform(50, 300),
                peak_response_time_ms=random.uniform(300, 1000),
                api_requests_count=random.randint(1000, 5000),
                failed_requests_count=random.randint(0, 50),
                success_rate_percentage=random.uniform(95, 100),
                cpu_usage_percentage=random.uniform(20, 80),
                memory_usage_mb=random.uniform(1000, 4000),
                disk_usage_gb=random.uniform(50, 200),
                active_connections=random.randint(10, 100),
                db_query_count=random.randint(5000, 15000),
                db_slow_queries_count=random.randint(0, 10),
                db_connection_pool_size=random.randint(10, 50),
                ai_requests_count=random.randint(100, 500),
                ai_avg_processing_time_s=random.uniform(0.5, 3.0),
                ai_token_usage=random.randint(10000, 50000),
                ai_cost_usd=Decimal(str(round(random.uniform(0.5, 5.0), 4)))
            )
    print(f"✓ Created {days * 24} system metrics records")


def create_user_activity_analytics():
    """Create user activity analytics"""
    print("Creating user activity analytics...")
    users = User.objects.all()[:10]
    now = timezone.now().date()
    
    count = 0
    for user in users:
        for day in range(30):
            date = now - timedelta(days=day)
            
            engagement_score = random.uniform(50, 100)
            anomaly = random.random() < 0.1  # 10% chance of anomaly
            
            UserActivityAnalytics.objects.create(
                user=user,
                date=date,
                login_count=random.randint(0, 5),
                total_session_duration_minutes=random.randint(0, 480),
                avg_session_duration_minutes=random.uniform(30, 120),
                features_used_count=random.randint(0, 10),
                features_used_list=['PID Analysis', 'CRS', 'Drawing Upload'][:random.randint(0, 3)],
                engagement_score=engagement_score,
                productivity_score=random.uniform(40, 100),
                risk_score=random.uniform(0, 50) if not anomaly else random.uniform(60, 100),
                drawings_uploaded=random.randint(0, 10),
                analyses_completed=random.randint(0, 20),
                reports_generated=random.randint(0, 5),
                documents_accessed=random.randint(0, 30),
                usage_pattern='power_user' if engagement_score > 80 else 'normal',
                anomaly_detected=anomaly,
                anomaly_details={'reason': 'Unusual access pattern'} if anomaly else {},
                recommendations=['Increase feature adoption'] if engagement_score < 60 else []
            )
            count += 1
    print(f"✓ Created {count} user activity analytics records")


def create_security_alerts():
    """Create security alerts"""
    print("Creating security alerts...")
    users = list(User.objects.all()[:5])
    
    alert_types = [
        ('Failed Login Attempts', 'Multiple failed login attempts detected', 'medium'),
        ('Unusual Access Pattern', 'Access from unusual location or time', 'high'),
        ('Brute Force Attack', 'Potential brute force attack detected', 'critical'),
        ('Suspicious Activity', 'Unusual user behavior detected', 'medium'),
        ('Data Exfiltration Attempt', 'Large data download detected', 'critical'),
    ]
    
    count = 0
    for _ in range(15):
        alert_type, description, severity = random.choice(alert_types)
        user = random.choice(users) if random.random() > 0.3 else None
        
        SecurityAlert.objects.create(
            alert_type=alert_type,
            severity=severity,
            status=random.choice(['new', 'investigating', 'resolved']),
            title=alert_type,
            description=description,
            detection_time=timezone.now() - timedelta(days=random.randint(0, 7)),
            user=user,
            ip_address=f"192.168.{random.randint(1, 255)}.{random.randint(1, 255)}",
            user_agent="Mozilla/5.0...",
            ai_confidence=random.uniform(0.6, 1.0),
            threat_indicators=['Rapid requests', 'Geographic anomaly'][:random.randint(1, 2)],
            recommended_actions=['Block IP', 'Reset password', 'Contact user'][:random.randint(1, 3)]
        )
        count += 1
    print(f"✓ Created {count} security alerts")


def create_predictive_insights():
    """Create AI predictions"""
    print("Creating predictive insights...")
    
    insights = [
        {
            'insight_type': 'usage_forecast',
            'title': 'Expected 30% Increase in User Activity',
            'description': 'Based on historical patterns, user activity is predicted to increase by 30% next month.',
            'impact_level': 'high',
            'affected_area': 'System Resources',
            'recommendations': [
                'Scale up server capacity',
                'Optimize database queries',
                'Implement caching strategy'
            ],
            'estimated_benefit': 'Prevent system slowdowns and maintain 99.9% uptime'
        },
        {
            'insight_type': 'user_churn_risk',
            'title': '5 Users at Risk of Churning',
            'description': 'Machine learning models identified 5 users with declining engagement patterns.',
            'impact_level': 'medium',
            'affected_area': 'User Retention',
            'recommendations': [
                'Send re-engagement email campaign',
                'Offer personalized training',
                'Schedule check-in calls'
            ],
            'estimated_benefit': 'Retain $50,000 in annual recurring revenue'
        },
        {
            'insight_type': 'performance_optimization',
            'title': 'API Response Time Can Be Reduced by 40%',
            'description': 'Analysis shows database query optimization can significantly improve performance.',
            'impact_level': 'high',
            'affected_area': 'System Performance',
            'recommendations': [
                'Add database indexes',
                'Implement query caching',
                'Optimize N+1 queries'
            ],
            'estimated_benefit': 'Improve user experience and reduce server costs'
        },
        {
            'insight_type': 'capacity_planning',
            'title': 'Storage Capacity Will Reach 80% in 60 Days',
            'description': 'Current storage usage growth rate suggests capacity planning is needed.',
            'impact_level': 'medium',
            'affected_area': 'Storage Infrastructure',
            'recommendations': [
                'Plan storage upgrade',
                'Implement data archival',
                'Compress old files'
            ],
            'estimated_benefit': 'Avoid service disruption and emergency upgrades'
        },
        {
            'insight_type': 'cost_optimization',
            'title': 'AI Token Usage Can Be Reduced by 25%',
            'description': 'Optimize AI prompts and implement caching to reduce token consumption.',
            'impact_level': 'medium',
            'affected_area': 'Operational Costs',
            'recommendations': [
                'Cache common AI responses',
                'Optimize prompt templates',
                'Implement rate limiting'
            ],
            'estimated_benefit': 'Save $1,200 monthly on AI API costs'
        }
    ]
    
    count = 0
    for insight_data in insights:
        PredictiveInsight.objects.create(
            insight_type=insight_data['insight_type'],
            title=insight_data['title'],
            description=insight_data['description'],
            prediction_date=timezone.now().date() + timedelta(days=random.randint(7, 90)),
            confidence_score=random.uniform(0.75, 0.95),
            predicted_values={'forecast': random.randint(1000, 5000)},
            impact_level=insight_data['impact_level'],
            affected_area=insight_data['affected_area'],
            recommendations=insight_data['recommendations'],
            action_items=[f"Action {i+1}" for i in range(3)],
            estimated_benefit=insight_data['estimated_benefit'],
            ml_model_used='Random Forest Regressor v2.1',
            training_data_period='Last 90 days',
            is_active=True,
            is_acknowledged=False
        )
        count += 1
    print(f"✓ Created {count} predictive insights")


def create_feature_usage_analytics():
    """Create feature usage analytics"""
    print("Creating feature usage analytics...")
    
    features = ['PID Analysis', 'Comment Resolution Sheet', 'Drawing Upload', 'Report Generation', 'User Management']
    now = timezone.now().date()
    
    count = 0
    for feature in features:
        for day in range(30):
            date = now - timedelta(days=day)
            active_users = random.randint(20, 100)
            
            FeatureUsageAnalytics.objects.create(
                feature_name=feature,
                date=date,
                total_users=100,
                active_users=active_users,
                new_users=random.randint(0, 10),
                returning_users=active_users - random.randint(0, 10),
                total_usage_count=random.randint(100, 500),
                avg_usage_per_user=random.uniform(2.0, 10.0),
                success_rate=random.uniform(95, 100),
                avg_completion_time_s=random.uniform(5.0, 30.0),
                adoption_rate_percentage=random.uniform(20, 100),
                growth_rate_percentage=random.uniform(-5, 15),
                retention_rate_percentage=random.uniform(70, 95),
                health_score=random.uniform(70, 100),
                trend=random.choice(['growing', 'stable', 'declining']),
                insights=[f"Usage increased on weekdays"]
            )
            count += 1
    print(f"✓ Created {count} feature usage analytics records")


def create_error_log_analytics():
    """Create error log analytics"""
    print("Creating error log analytics...")
    
    errors = [
        ('DatabaseError', 'Connection timeout', 'Database', 'high'),
        ('ValidationError', 'Invalid input format', 'API', 'medium'),
        ('PermissionDenied', 'Unauthorized access attempt', 'Security', 'high'),
        ('TimeoutError', 'Request timeout', 'External Service', 'medium'),
        ('FileNotFoundError', 'Missing resource', 'Storage', 'low'),
    ]
    
    count = 0
    for error_type, message, feature, severity in errors:
        ErrorLogAnalytics.objects.create(
            error_type=error_type,
            error_message=message,
            first_occurrence=timezone.now() - timedelta(days=random.randint(1, 30)),
            last_occurrence=timezone.now() - timedelta(hours=random.randint(1, 24)),
            occurrence_count=random.randint(1, 50),
            affected_users_count=random.randint(1, 20),
            endpoint=f'/api/{feature.lower()}',
            feature=feature,
            stack_trace='...',
            severity=severity,
            root_cause_analysis=f'AI Analysis: {message} is likely caused by...',
            suggested_fix=f'Consider implementing retry logic and connection pooling',
            similar_issues=[],
            status=random.choice(['open', 'investigating', 'resolved'])
        )
        count += 1
    print(f"✓ Created {count} error log analytics records")


def create_system_health_checks():
    """Create system health checks"""
    print("Creating system health checks...")
    now = timezone.now()
    
    count = 0
    for hour in range(24):
        timestamp = now - timedelta(hours=hour)
        overall_healthy = random.random() > 0.1  # 90% healthy
        
        SystemHealthCheck.objects.create(
            check_time=timestamp,
            database_status='healthy' if overall_healthy else random.choice(['healthy', 'degraded']),
            redis_status='healthy',
            celery_status='healthy' if overall_healthy else 'degraded',
            storage_status='healthy',
            api_status='healthy' if overall_healthy else 'degraded',
            overall_status='healthy' if overall_healthy else 'degraded',
            health_score=random.uniform(95, 100) if overall_healthy else random.uniform(60, 95),
            response_times={
                'database': random.randint(5, 50),
                'redis': random.randint(1, 10),
                'api': random.randint(50, 200)
            },
            error_rates={
                'api': random.uniform(0, 5),
                'database': random.uniform(0, 2)
            },
            resource_usage={
                'cpu': random.uniform(20, 80),
                'memory': random.uniform(1000, 4000),
                'disk': random.uniform(50, 200)
            },
            issues_found=['Slow query detected'] if not overall_healthy else [],
            warnings=['High memory usage'] if random.random() < 0.2 else [],
            recommendations=['Optimize database queries'] if not overall_healthy else []
        )
        count += 1
    print(f"✓ Created {count} system health check records")


def main():
    """Generate all sample data"""
    print("=" * 60)
    print("Generating AI Analytics Sample Data")
    print("=" * 60)
    
    try:
        create_system_metrics(days=7)
        create_user_activity_analytics()
        create_security_alerts()
        create_predictive_insights()
        create_feature_usage_analytics()
        create_error_log_analytics()
        create_system_health_checks()
        
        print("\n" + "=" * 60)
        print("✓ Sample data generation complete!")
        print("=" * 60)
        print("\nYou can now:")
        print("1. Navigate to http://localhost:3000/admin")
        print("2. Explore the AI-powered analytics tabs")
        print("3. View real-time insights and predictions")
        
    except Exception as e:
        print(f"\n✗ Error generating data: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
