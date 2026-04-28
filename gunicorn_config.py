# Gunicorn configuration for P&ID OCR Processing
# Optimized for long-running AI/ML tasks

import multiprocessing

# Server socket
bind = "0.0.0.0:8000"
backlog = 2048

# Worker processes
# SOFT-CODED: Keep workers low on Railway to avoid OOM (OCR tasks are memory-heavy).
# Each gthread worker can handle 4 concurrent connections via threads.
# 2 workers × 4 threads = 8 concurrent connections — sufficient for API load.
workers = 2
worker_class = "gthread"  # Use threaded workers for better connection handling
threads = 4  # 4 threads per worker
worker_connections = 1000
max_requests = 500          # Recycle workers after 500 requests to prevent memory leaks
max_requests_jitter = 50    # Spread recycling to avoid simultaneous restarts

# Timeout settings - CRITICAL for P&ID processing
timeout = 1200  # 20 minutes for P&ID OCR + AI processing
graceful_timeout = 120  # 2 minutes for graceful shutdown
keepalive = 75  # Keep connections alive for 75 seconds (fixes ECONNRESET)

# Logging
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = "aiflow_backend"

# Server mechanics
daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# Performance tuning
preload_app = False  # Don't preload - models are heavy
sendfile = True
reuse_port = True
