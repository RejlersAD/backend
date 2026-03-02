#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection

sql = """
CREATE TABLE IF NOT EXISTS designiq_processed_pid_outputs (
    id SERIAL PRIMARY KEY,
    project_id INTEGER,
    pid_number VARCHAR(255),
    pid_revision VARCHAR(50),
    list_type VARCHAR(50) DEFAULT 'line_list',
    document_id VARCHAR(255),
    processed_by_id INTEGER,
    processing_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    excel_filename VARCHAR(500),
    excel_file VARCHAR(100),
    file_size INTEGER DEFAULT 0,
    total_lines INTEGER DEFAULT 0,
    total_columns INTEGER DEFAULT 0,
    format_type VARCHAR(50),
    enrichment_enabled BOOLEAN DEFAULT FALSE,
    data JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_processed_pid_output_pid_number 
    ON designiq_processed_pid_outputs(pid_number);
CREATE INDEX IF NOT EXISTS idx_processed_pid_output_list_type 
    ON designiq_processed_pid_outputs(list_type);
CREATE INDEX IF NOT EXISTS idx_processed_pid_output_processing_date 
    ON designiq_processed_pid_outputs(processing_date);
CREATE INDEX IF NOT EXISTS idx_processed_pid_output_project_id 
    ON designiq_processed_pid_outputs(project_id);
"""

with connection.cursor() as cursor:
    cursor.execute(sql)
    print("✓ Table created successfully")

# Verify
from apps.designiq.models import ProcessedPIDOutput
count = ProcessedPIDOutput.objects.count()
print(f"✓ Verified - Current record count: {count}")
