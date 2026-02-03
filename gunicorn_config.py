# Gunicorn configuration for P&ID OCR Processing
# Optimized for long-running AI/ML tasks

import multiprocessing

# Server socket
bind = "0.0.0.0:8000"
backlog = 2048

# Worker processes
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"  # Use sync for CPU-intensive OCR tasks
worker_connections = 1000
max_requests = 1000
max_requests_jitter = 50

# Timeout settings - CRITICAL for P&ID processing
timeout = 1200  # 20 minutes for P&ID OCR + AI processing
graceful_timeout = 120  # 2 minutes for graceful shutdown
keepalive = 5

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
