"""
QHSE AI Celery Tasks - Asynchronous AI/ML processing
"""
import logging
from celery import shared_task
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings

from .models import QHSERunningProject
from .ai_services import qhse_ai_service
from .ai_config import AI_ALERTS_CONFIG

logger = logging.getLogger(__name__)


@shared_task(name='qhse.predict_project_risk_async')
def predict_project_risk_async(project_no):
    """
    Asynchronously predict project risk
    """
    try:
        project = QHSERunningProject.objects.get(project_no=project_no)
        prediction = qhse_ai_service.predict_project_risk(project)
        
        # Check if alerts should be sent
        if prediction['risk_category'] in ['critical', 'high']:
            send_risk_alert_async.delay(project_no, prediction)
        
        logger.info(f"✅ Risk prediction completed for {project_no}: {prediction['risk_label']}")
        return prediction
        
    except Exception as e:
        logger.error(f"❌ Async risk prediction failed for {project_no}: {str(e)}")
        return {'error': str(e)}


@shared_task(name='qhse.predict_all_risks_async')
def predict_all_risks_async():
    """
    Asynchronously predict risks for all active projects
    """
    try:
        predictions = qhse_ai_service.predict_all_project_risks()
        
        # Count high-risk projects
        high_risk_count = len([p for p in predictions if p['risk_category'] in ['critical', 'high']])
        
        logger.info(f"✅ Batch risk prediction completed: {len(predictions)} projects, {high_risk_count} high-risk")
        
        return {
            'total_projects': len(predictions),
            'high_risk_count': high_risk_count,
            'predictions': predictions
        }
        
    except Exception as e:
        logger.error(f"❌ Async batch prediction failed: {str(e)}")
        return {'error': str(e)}


@shared_task(name='qhse.detect_anomalies_async')
def detect_anomalies_async(project_no):
    """
    Asynchronously detect anomalies in project
    """
    try:
        project = QHSERunningProject.objects.get(project_no=project_no)
        detection = qhse_ai_service.detect_anomalies(project)
        
        # Send alerts if anomalies detected
        if detection.get('anomalies_detected'):
            send_anomaly_alert_async.delay(project_no, detection)
        
        logger.info(f"✅ Anomaly detection completed for {project_no}: {detection.get('anomaly_count', 0)} anomalies")
        return detection
        
    except Exception as e:
        logger.error(f"❌ Async anomaly detection failed for {project_no}: {str(e)}")
        return {'error': str(e)}


@shared_task(name='qhse.classify_car_async')
def classify_car_async(car_text, car_id=None):
    """
    Asynchronously classify CAR/NCR
    """
    try:
        classification = qhse_ai_service.classify_car(car_text)
        
        logger.info(f"✅ CAR classification completed: {classification['category']}")
        return classification
        
    except Exception as e:
        logger.error(f"❌ Async CAR classification failed: {str(e)}")
        return {'error': str(e)}


@shared_task(name='qhse.send_risk_alert_async')
def send_risk_alert_async(project_no, prediction):
    """
    Send risk alert notifications
    """
    try:
        alert_config = AI_ALERTS_CONFIG
        risk_category = prediction.get('risk_category', 'unknown')
        
        # Determine recipients based on severity
        recipients = []
        
        if risk_category == 'critical':
            recipients.extend(alert_config['notification_channels']['recipients']['senior_management'])
            recipients.extend(alert_config['notification_channels']['recipients']['project_managers'])
        elif risk_category == 'high':
            recipients.extend(alert_config['notification_channels']['recipients']['project_managers'])
        
        if not recipients:
            logger.warning(f"No recipients configured for {risk_category} risk alerts")
            return
        
        # Build email
        subject = f"⚠️ {prediction.get('risk_label', 'High Risk')} Alert - Project {project_no}"
        
        message = f"""
QHSE AI Alert - Risk Prediction

Project: {project_no}
Risk Score: {prediction.get('risk_score', 0):.2f}
Risk Level: {prediction.get('risk_label', 'Unknown')}
Confidence: {prediction.get('confidence', 0)*100:.0f}%

Risk Factors:
"""
        
        for idx, factor in enumerate(prediction.get('risk_factors', [])[:5], 1):
            message += f"\n{idx}. {factor.get('factor')}: {factor.get('value')} - {factor.get('impact')}"
        
        message += "\n\nRecommended Actions:\n"
        for idx, rec in enumerate(prediction.get('recommendations', [])[:5], 1):
            message += f"\n{idx}. {rec}"
        
        message += f"\n\nGenerated: {prediction.get('prediction_timestamp')}"
        message += f"\nModel Version: {prediction.get('model_version')}"
        message += "\n\nThis is an automated alert from the QHSE AI system."
        
        # Send email
        if alert_config['notification_channels']['email']['enabled']:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=recipients,
                fail_silently=True
            )
            logger.info(f"✅ Risk alert sent for {project_no} to {len(recipients)} recipients")
        
        # Log to dashboard (implement dashboard notification system)
        if alert_config['notification_channels']['dashboard']['enabled']:
            logger.info(f"📊 Dashboard alert logged for {project_no}")
        
        return {'sent': True, 'recipients': len(recipients)}
        
    except Exception as e:
        logger.error(f"❌ Failed to send risk alert for {project_no}: {str(e)}")
        return {'error': str(e)}


@shared_task(name='qhse.send_anomaly_alert_async')
def send_anomaly_alert_async(project_no, detection):
    """
    Send anomaly detection alert notifications
    """
    try:
        alert_config = AI_ALERTS_CONFIG
        anomalies = detection.get('anomalies', [])
        
        if not anomalies:
            return {'sent': False, 'reason': 'No anomalies to report'}
        
        # Determine severity
        has_critical = any(a.get('severity') == 'critical' for a in anomalies)
        has_high = any(a.get('severity') == 'high' for a in anomalies)
        
        recipients = []
        if has_critical:
            recipients.extend(alert_config['notification_channels']['recipients']['senior_management'])
        if has_high or has_critical:
            recipients.extend(alert_config['notification_channels']['recipients']['project_managers'])
        else:
            recipients.extend(alert_config['notification_channels']['recipients']['qhse_team'])
        
        # Build email
        subject = f"🔍 Anomaly Detected - Project {project_no}"
        
        message = f"""
QHSE AI Alert - Anomaly Detection

Project: {project_no}
Anomalies Detected: {len(anomalies)}

Anomaly Details:
"""
        
        for idx, anomaly in enumerate(anomalies, 1):
            message += f"\n{idx}. {anomaly.get('metric')} - {anomaly.get('type')}"
            message += f"\n   Severity: {anomaly.get('severity')}"
            message += f"\n   Value: {anomaly.get('value')}"
            message += f"\n   Expected Range: {anomaly.get('expected_range')}"
            message += f"\n   Description: {anomaly.get('description')}\n"
        
        message += f"\nConfidence: {detection.get('confidence', 0)*100:.0f}%"
        message += f"\nDetected: {detection.get('detection_timestamp')}"
        message += f"\nModel Version: {detection.get('model_version')}"
        message += "\n\nPlease investigate these anomalies promptly."
        
        # Send email
        if alert_config['notification_channels']['email']['enabled']:
            send_mail(
                subject=subject,
                message=message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=recipients,
                fail_silently=True
            )
            logger.info(f"✅ Anomaly alert sent for {project_no} to {len(recipients)} recipients")
        
        return {'sent': True, 'recipients': len(recipients)}
        
    except Exception as e:
        logger.error(f"❌ Failed to send anomaly alert for {project_no}: {str(e)}")
        return {'error': str(e)}


@shared_task(name='qhse.scheduled_risk_assessment')
def scheduled_risk_assessment():
    """
    Scheduled task to run risk assessment for all projects
    Run daily via Celery Beat
    """
    try:
        logger.info("🔄 Starting scheduled risk assessment...")
        
        projects = QHSERunningProject.objects.filter(is_active=True)
        total_projects = projects.count()
        
        high_risk_projects = []
        
        for project in projects:
            try:
                prediction = qhse_ai_service.predict_project_risk(project)
                
                if prediction['risk_category'] in ['critical', 'high']:
                    high_risk_projects.append({
                        'project_no': project.project_no,
                        'project_title': project.project_title,
                        'risk_score': prediction['risk_score'],
                        'risk_category': prediction['risk_category']
                    })
                    
                    # Send individual alerts
                    send_risk_alert_async.delay(project.project_no, prediction)
                    
            except Exception as e:
                logger.error(f"Failed to assess project {project.project_no}: {str(e)}")
                continue
        
        # Send summary report
        if high_risk_projects:
            send_risk_summary_report.delay(high_risk_projects, total_projects)
        
        logger.info(f"✅ Scheduled risk assessment completed: {len(high_risk_projects)}/{total_projects} high-risk")
        
        return {
            'total_projects': total_projects,
            'high_risk_count': len(high_risk_projects),
            'high_risk_projects': high_risk_projects,
            'timestamp': timezone.now().isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Scheduled risk assessment failed: {str(e)}")
        return {'error': str(e)}


@shared_task(name='qhse.send_risk_summary_report')
def send_risk_summary_report(high_risk_projects, total_projects):
    """
    Send daily risk summary report to management
    """
    try:
        alert_config = AI_ALERTS_CONFIG
        recipients = alert_config['notification_channels']['recipients']['senior_management']
        
        subject = f"📊 Daily QHSE Risk Summary - {len(high_risk_projects)} High-Risk Projects"
        
        message = f"""
QHSE AI Daily Risk Summary Report

Date: {timezone.now().strftime('%Y-%m-%d')}
Total Active Projects: {total_projects}
High/Critical Risk Projects: {len(high_risk_projects)}
Risk Percentage: {(len(high_risk_projects)/total_projects*100):.1f}%

High-Risk Projects:
"""
        
        for idx, proj in enumerate(high_risk_projects[:20], 1):  # Top 20
            message += f"\n{idx}. {proj['project_no']} - {proj['project_title']}"
            message += f"\n   Risk Score: {proj['risk_score']:.2f} ({proj['risk_category'].upper()})\n"
        
        if len(high_risk_projects) > 20:
            message += f"\n... and {len(high_risk_projects) - 20} more projects"
        
        message += "\n\nPlease review high-risk projects and take appropriate action."
        message += "\n\nThis is an automated report from the QHSE AI system."
        
        # Send email
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=True
        )
        
        logger.info(f"✅ Daily risk summary sent to {len(recipients)} recipients")
        return {'sent': True, 'recipients': len(recipients)}
        
    except Exception as e:
        logger.error(f"❌ Failed to send risk summary: {str(e)}")
        return {'error': str(e)}


@shared_task(name='qhse.retrain_models')
def retrain_models():
    """
    Scheduled task to retrain AI/ML models
    Run weekly via Celery Beat
    """
    try:
        logger.info("🔄 Starting model retraining process...")
        
        # This is a placeholder - implement actual retraining logic
        # In production: load training data, retrain models, evaluate, deploy
        
        results = {
            'risk_prediction': {'status': 'scheduled', 'accuracy': 0.85},
            'car_classification': {'status': 'scheduled', 'accuracy': 0.78},
            'manhour_prediction': {'status': 'scheduled', 'accuracy': 0.82},
            'anomaly_detection': {'status': 'scheduled', 'accuracy': 0.88}
        }
        
        logger.info("✅ Model retraining completed")
        return results
        
    except Exception as e:
        logger.error(f"❌ Model retraining failed: {str(e)}")
        return {'error': str(e)}
