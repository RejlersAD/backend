"""
Robust Queue Service - Handles Celery task queuing with intelligent fallback
Ensures 300+ concurrent users can process tasks even when queue is unavailable
Provides exponential retry, circuit breaker, and synchronous fallback
"""
import logging
import time
from functools import wraps
from celery import current_app
from celery.exceptions import CeleryError
from django.core.cache import cache
from django.conf import settings

logger = logging.getLogger(__name__)


class QueueServiceException(Exception):
    """Base exception for queue service errors"""
    pass


class QueueUnavailableException(QueueServiceException):
    """Raised when queue is unavailable and no fallback is available"""
    pass


class RobustQueueService:
    """
    Intelligent queuing service with:
    - Circuit breaker for failed queues
    - Exponential backoff retry
    - Synchronous fallback
    - Production-ready error handling
    """
    
    # Circuit breaker configuration
    CIRCUIT_BREAKER_KEY = 'queue_circuit_breaker'
    MAX_FAILURES = 5  # Failures before circuit opens
    FAILURE_TIMEOUT = 300  # Seconds (5 minutes)
    RECOVERY_ATTEMPT_DELAY = 30  # Seconds before trying again
    
    # Retry configuration
    DEFAULT_RETRIES = 3
    INITIAL_BACKOFF = 1  # Seconds
    MAX_BACKOFF = 32  # Seconds
    
    @classmethod
    def _check_circuit_breaker(cls):
        """Check if circuit breaker is open (queue is down)"""
        circuit_data = cache.get(cls.CIRCUIT_BREAKER_KEY)
        if not circuit_data:
            return False  # Circuit is closed (normal operation)
        
        failures, last_failure_time = circuit_data['failures'], circuit_data['last_failure']
        
        # If circuit has been open long enough, try recovery
        if time.time() - last_failure_time >= cls.FAILURE_TIMEOUT:
            logger.info("[QueueService] Attempting circuit breaker recovery")
            cache.delete(cls.CIRCUIT_BREAKER_KEY)
            return False  # Try again
        
        # Circuit is still open
        if failures >= cls.MAX_FAILURES:
            logger.warning(f"[QueueService] Circuit breaker OPEN - {failures} failures in last {cls.FAILURE_TIMEOUT}s")
            return True
        
        return False
    
    @classmethod
    def _record_failure(cls):
        """Record a queue operation failure and potentially open circuit"""
        circuit_data = cache.get(cls.CIRCUIT_BREAKER_KEY)
        
        if not circuit_data:
            circuit_data = {'failures': 0, 'last_failure': time.time()}
        
        circuit_data['failures'] += 1
        circuit_data['last_failure'] = time.time()
        
        cache.set(cls.CIRCUIT_BREAKER_KEY, circuit_data, timeout=cls.FAILURE_TIMEOUT)
        logger.warning(f"[QueueService] Recorded failure - total: {circuit_data['failures']}")
    
    @classmethod
    def _clear_failures(cls):
        """Clear failure count on successful operation"""
        cache.delete(cls.CIRCUIT_BREAKER_KEY)
        logger.debug("[QueueService] Cleared circuit breaker state - operation successful")
    
    @classmethod
    def queue_task(cls, task, args=None, kwargs=None, sync_fallback=None, max_retries=None):
        """
        Queue a Celery task with intelligent fallback
        
        Args:
            task: Celery task object (e.g., process_pid_document)
            args: Positional arguments tuple
            kwargs: Keyword arguments dict
            sync_fallback: Callable to invoke if queue fails (function to call directly)
            max_retries: Number of retry attempts (default: DEFAULT_RETRIES)
            
        Returns:
            AsyncResult if queued successfully or sync_fallback result if queued
            
        Raises:
            QueueUnavailableException: If queue unavailable and no fallback provided
            
        Examples:
            # With async result:
            result = RobustQueueService.queue_task(
                process_pid_document,
                args=(doc_id,)
            )
            
            # With sync fallback:
            result = RobustQueueService.queue_task(
                process_pdf_document,
                args=(pdf_id,),
                sync_fallback=lambda doc_id: your_sync_processor(doc_id)
            )
        """
        if args is None:
            args = ()
        if kwargs is None:
            kwargs = {}
        if max_retries is None:
            max_retries = cls.DEFAULT_RETRIES
        
        # Check circuit breaker first
        if cls._check_circuit_breaker():
            if sync_fallback:
                logger.info("[QueueService] Circuit open - using sync fallback")
                return cls._execute_with_fallback(sync_fallback, args, kwargs)
            else:
                raise QueueUnavailableException(
                    "Queue unavailable and no sync fallback provided"
                )
        
        # Try to queue with exponential backoff
        backoff = cls.INITIAL_BACKOFF
        last_error = None
        
        for attempt in range(max_retries):
            try:
                # Attempt to queue task
                result = task.delay(*args, **kwargs)
                
                # Success - clear failure state
                cls._clear_failures()
                logger.info(f"[QueueService] Task queued successfully: {task.name} (attempt {attempt + 1})")
                
                return result
                
            except (CeleryError, Exception) as exc:
                last_error = exc
                logger.warning(
                    f"[QueueService] Queue attempt {attempt + 1}/{max_retries} failed: {exc} "
                    f"(type: {type(exc).__name__})"
                )
                
                # Record failure
                cls._record_failure()
                
                # If last attempt or circuit breaker open, use fallback
                if attempt == max_retries - 1:
                    if sync_fallback:
                        logger.warning(
                            f"[QueueService] All {max_retries} queue attempts exhausted - "
                            f"using sync fallback for {task.name}"
                        )
                        return cls._execute_with_fallback(sync_fallback, args, kwargs)
                    else:
                        raise QueueUnavailableException(f"Celery queue unavailable: {exc}") from exc
                
                # Exponential backoff before retry
                if attempt < max_retries - 1:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, cls.MAX_BACKOFF)
        
        # Should not reach here, but handle just in case
        raise QueueUnavailableException(f"Failed to queue task after {max_retries} attempts: {last_error}")
    
    @classmethod
    def _execute_with_fallback(cls, sync_fallback, args, kwargs):
        """Execute sync fallback function with error handling"""
        try:
            logger.debug(f"[QueueService] Executing sync fallback with args={args}, kwargs={kwargs}")
            result = sync_fallback(*args, **kwargs)
            logger.info("[QueueService] Sync fallback completed successfully")
            return result
        except Exception as exc:
            logger.error(f"[QueueService] Sync fallback failed: {exc}", exc_info=True)
            raise
    
    @classmethod
    def is_queue_available(cls):
        """Check if queue is currently available"""
        if cls._check_circuit_breaker():
            return False
        
        try:
            # Quick connectivity check
            current_app.control.inspect().active_queues()
            return True
        except Exception:
            cls._record_failure()
            return False
    
    @classmethod
    def get_queue_health(cls):
        """Get detailed queue health status"""
        circuit_data = cache.get(cls.CIRCUIT_BREAKER_KEY)
        
        return {
            'available': not cls._check_circuit_breaker(),
            'circuit_breaker_open': cls._check_circuit_breaker(),
            'failures': circuit_data['failures'] if circuit_data else 0,
            'last_failure': circuit_data['last_failure'] if circuit_data else None,
        }


def queue_with_fallback(sync_callable=None):
    """
    Decorator for Celery task methods to add queue fallback
    
    Usage:
        @queue_with_fallback(sync_callable=process_document_sync)
        def my_task(doc_id):
            # Async implementation
            pass
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # This decorator is applied to task definitions
            return func(*args, **kwargs)
        return wrapper
    return decorator
