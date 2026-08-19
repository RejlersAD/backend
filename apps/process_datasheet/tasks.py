"""
Celery Tasks for Process Datasheet
Background task processing for PDF extraction and datasheet operations
"""
from celery import shared_task
from django.core.mail import send_mail
from django.conf import settings
import logging
import time

from apps.process_datasheet.models import DatasheetExtractionJob, ProcessDatasheet
from apps.process_datasheet.services import CalculationService, ValidationService
from apps.process_datasheet.services.extraction_service import ExtractionService

logger = logging.getLogger(__name__)

# Get dynamic retry configuration from settings
MAX_RETRIES = getattr(settings, 'DATASHEET_MAX_RETRIES', 5)
TASK_TIMEOUT = getattr(settings, 'DATASHEET_TASK_TIMEOUT', 600)
RETRY_BACKOFF = getattr(settings, 'DATASHEET_RETRY_BACKOFF', 2)


@shared_task(bind=True, max_retries=MAX_RETRIES, time_limit=TASK_TIMEOUT, soft_time_limit=TASK_TIMEOUT-30)
def extract_datasheet_from_pdf(self, job_id: str, pdf_path: str, equipment_type_id: str):
    """
    Background task for PDF extraction with dynamic retry configuration
    
    Args:
        job_id: DatasheetExtractionJob UUID
        pdf_path: Path to PDF file
        equipment_type_id: Equipment type UUID
    """
    from apps.process_datasheet.models import DatasheetExtractionJob, EquipmentType
    
    try:
        # Update job status
        job = DatasheetExtractionJob.objects.get(pk=job_id)
        job.status = 'processing'
        job.save()
        
        logger.info(f"Starting extraction for job {job_id}")
        
        # Get equipment configuration
        equipment_type = EquipmentType.objects.get(pk=equipment_type_id)
        
        # Initialize extraction service
        extraction_service = ExtractionService()
        
        # Extract data with dynamic retries from settings
        result = extraction_service.extract_with_retry(
            pdf_path=pdf_path,
            equipment_config=equipment_type.configuration,
            max_retries=MAX_RETRIES
        )
        
        if result['success']:
            # Update job with results
            job.status = 'completed'
            job.extracted_data = result['data']
            job.confidence_scores = result['confidence']
            job.processing_time = time.time() - job.created_at.timestamp()
            job.save()
            
            logger.info(f"Extraction completed for job {job_id}")
            
            # Send notification
            if job.created_by:
                send_extraction_notification.delay(job_id, 'success')
            
            return {
                'success': True,
                'job_id': str(job_id),
                'method': result['method']
            }
        else:
            # Extraction failed
            job.status = 'failed'
            job.error_message = '; '.join(result.get('errors', ['Unknown error']))
            job.save()
            
            logger.error(f"Extraction failed for job {job_id}: {job.error_message}")
            
            # Send failure notification
            if job.created_by:
                send_extraction_notification.delay(job_id, 'failed')
            
            return {
                'success': False,
                'job_id': str(job_id),
                'errors': result.get('errors', [])
            }
            
    except Exception as e:
        logger.error(f"Task error for job {job_id}: {str(e)}")
        
        try:
            job = DatasheetExtractionJob.objects.get(pk=job_id)
            job.status = 'failed'
            job.error_message = str(e)
            job.save()
        except Exception:
            pass
        
        # Retry if possible with exponential backoff
        if self.request.retries < self.max_retries:
            # Exponential backoff: 2s, 4s, 8s, 16s, 32s...
            countdown = RETRY_BACKOFF ** self.request.retries
            logger.info(f"Retrying job {job_id} in {countdown} seconds (attempt {self.request.retries + 1}/{self.max_retries})")
            raise self.retry(exc=e, countdown=countdown)
        
        return {
            'success': False,
            'job_id': str(job_id),
            'error': str(e)
        }


@shared_task
def calculate_datasheet_values(datasheet_id: str):
    """
    Background task for datasheet calculations
    
    Args:
        datasheet_id: ProcessDatasheet UUID
    """
    try:
        datasheet = ProcessDatasheet.objects.get(pk=datasheet_id)
        equipment_config = datasheet.equipment_type.configuration
        
        logger.info(f"Calculating values for datasheet {datasheet.document_number}")
        
        # Run calculations
        calculated_values = CalculationService.calculate_all(
            datasheet.datasheet_data,
            equipment_config
        )
        
        # Update datasheet
        datasheet.calculated_values = calculated_values
        datasheet.save()
        
        logger.info(f"Calculations completed for {datasheet.document_number}")
        
        return {
            'success': True,
            'datasheet_id': str(datasheet_id),
            'calculations': len(calculated_values)
        }
        
    except Exception as e:
        logger.error(f"Calculation task error: {str(e)}")
        return {
            'success': False,
            'datasheet_id': str(datasheet_id),
            'error': str(e)
        }


@shared_task
def validate_datasheet(datasheet_id: str):
    """
    Background task for datasheet validation
    
    Args:
        datasheet_id: ProcessDatasheet UUID
    """
    try:
        datasheet = ProcessDatasheet.objects.get(pk=datasheet_id)
        equipment_config = datasheet.equipment_type.configuration
        
        logger.info(f"Validating datasheet {datasheet.document_number}")
        
        # Run validation
        validation_results = ValidationService.validate_all(
            datasheet.datasheet_data,
            equipment_config
        )
        
        # Update datasheet
        datasheet.validation_status = 'valid' if validation_results['valid'] else 'invalid'
        datasheet.validation_results = validation_results
        datasheet.validation_score = validation_results['score']
        datasheet.save()
        
        logger.info(f"Validation completed for {datasheet.document_number}: Score {validation_results['score']}")
        
        return {
            'success': True,
            'datasheet_id': str(datasheet_id),
            'score': validation_results['score'],
            'valid': validation_results['valid']
        }
        
    except Exception as e:
        logger.error(f"Validation task error: {str(e)}")
        return {
            'success': False,
            'datasheet_id': str(datasheet_id),
            'error': str(e)
        }


@shared_task
def process_datasheet_complete(datasheet_id: str):
    """
    Complete datasheet processing pipeline
    Runs extraction → calculation → validation in sequence
    
    Args:
        datasheet_id: ProcessDatasheet UUID
    """
    try:
        datasheet = ProcessDatasheet.objects.get(pk=datasheet_id)
        
        logger.info(f"Starting complete processing for {datasheet.document_number}")
        
        # Step 1: Calculate values
        calc_result = calculate_datasheet_values(datasheet_id)
        
        if not calc_result['success']:
            logger.warning(f"Calculations had errors, continuing with validation")
        
        # Step 2: Validate datasheet
        val_result = validate_datasheet(datasheet_id)
        
        if not val_result['success']:
            logger.error(f"Validation failed for {datasheet.document_number}")
            return {
                'success': False,
                'datasheet_id': str(datasheet_id),
                'stage': 'validation',
                'error': val_result.get('error')
            }
        
        # Step 3: Update status if fully valid
        if val_result['valid'] and val_result['score'] >= 90:
            datasheet.status = 'ready_for_review'
            datasheet.save()
        
        logger.info(f"Complete processing finished for {datasheet.document_number}")
        
        # Send notification
        if datasheet.prepared_by:
            send_processing_notification.delay(datasheet_id, val_result['score'])
        
        return {
            'success': True,
            'datasheet_id': str(datasheet_id),
            'validation_score': val_result['score'],
            'status': datasheet.status
        }
        
    except Exception as e:
        logger.error(f"Complete processing error: {str(e)}")
        return {
            'success': False,
            'datasheet_id': str(datasheet_id),
            'error': str(e)
        }


@shared_task
def bulk_validate_datasheets(datasheet_ids: list):
    """
    Validate multiple datasheets in parallel
    
    Args:
        datasheet_ids: List of ProcessDatasheet UUIDs
    """
    results = []
    
    for datasheet_id in datasheet_ids:
        result = validate_datasheet(datasheet_id)
        results.append(result)
    
    success_count = sum(1 for r in results if r['success'])
    
    logger.info(f"Bulk validation completed: {success_count}/{len(datasheet_ids)} successful")
    
    return {
        'total': len(datasheet_ids),
        'successful': success_count,
        'failed': len(datasheet_ids) - success_count,
        'results': results
    }


@shared_task
def send_extraction_notification(job_id: str, status: str):
    """
    Send email notification for extraction job completion
    
    Args:
        job_id: DatasheetExtractionJob UUID
        status: 'success' or 'failed'
    """
    try:
        from apps.process_datasheet.models import DatasheetExtractionJob
        
        job = DatasheetExtractionJob.objects.get(pk=job_id)
        
        if not job.created_by or not job.created_by.email:
            return
        
        if status == 'success':
            subject = f"✓ Datasheet Extraction Completed - Job {job_id[:8]}"
            message = f"""
Hello {job.created_by.first_name},

Your datasheet extraction job has been completed successfully!

Job ID: {job_id}
Equipment Type: {job.equipment_type.name}
Processing Time: {job.processing_time:.2f} seconds
Average Confidence: {sum(job.confidence_scores.values()) / len(job.confidence_scores) * 100 if job.confidence_scores else 0:.1f}%

You can now review the extracted data and create a datasheet.

Best regards,
AIFlow Process Datasheet System
            """
        else:
            subject = f"✗ Datasheet Extraction Failed - Job {job_id[:8]}"
            message = f"""
Hello {job.created_by.first_name},

Unfortunately, your datasheet extraction job has failed.

Job ID: {job_id}
Error: {job.error_message}

Please check the PDF file and try again, or contact support if the issue persists.

Best regards,
AIFlow Process Datasheet System
            """
        
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[job.created_by.email],
            fail_silently=True
        )
        
        logger.info(f"Notification sent to {job.created_by.email} for job {job_id}")
        
    except Exception as e:
        logger.error(f"Failed to send notification: {str(e)}")


@shared_task
def send_processing_notification(datasheet_id: str, validation_score: float):
    """
    Send email notification for datasheet processing completion
    
    Args:
        datasheet_id: ProcessDatasheet UUID
        validation_score: Validation score percentage
    """
    try:
        datasheet = ProcessDatasheet.objects.get(pk=datasheet_id)
        
        if not datasheet.prepared_by or not datasheet.prepared_by.email:
            return
        
        subject = f"Datasheet Processing Complete - {datasheet.document_number}"
        message = f"""
Hello {datasheet.prepared_by.first_name},

Your datasheet has been processed successfully!

Document Number: {datasheet.document_number}
Tag Number: {datasheet.tag_number}
Validation Score: {validation_score:.1f}%
Status: {datasheet.get_status_display()}

Calculations: {len(datasheet.calculated_values)} formulas executed
Validation: {'Passed' if validation_score >= 90 else 'Needs Review'}

Next Steps:
{f'✓ Ready for technical review' if validation_score >= 90 else '⚠ Please review validation errors and update datasheet'}

Best regards,
AIFlow Process Datasheet System
        """
        
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[datasheet.prepared_by.email],
            fail_silently=True
        )
        
        logger.info(f"Processing notification sent to {datasheet.prepared_by.email}")
        
    except Exception as e:
        logger.error(f"Failed to send processing notification: {str(e)}")


@shared_task
def cleanup_old_extraction_jobs(days_old: int = 30):
    """
    Cleanup old extraction jobs to free up storage
    
    Args:
        days_old: Delete jobs older than this many days
    """
    from django.utils import timezone
    from datetime import timedelta
    
    try:
        cutoff_date = timezone.now() - timedelta(days=days_old)
        
        old_jobs = DatasheetExtractionJob.objects.filter(
            created_at__lt=cutoff_date,
            status__in=['completed', 'failed']
        )
        
        count = old_jobs.count()
        old_jobs.delete()
        
        logger.info(f"Cleaned up {count} old extraction jobs")
        
        return {
            'success': True,
            'deleted_count': count
        }
        
    except Exception as e:
        logger.error(f"Cleanup task error: {str(e)}")
        return {
            'success': False,
            'error': str(e)
        }
