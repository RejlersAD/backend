#!/usr/bin/env python
"""
Quick script to create ProcessedPIDOutput table in Docker container
"""

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.db import connection

create_table_sql = """
CREATE TABLE IF NOT EXISTS designiq_processed_pid_outputs (
    id SERIAL PRIMARY KEY,
    project_id INTEGER REFERENCES core_project(id) ON DELETE SET NULL,
    pid_number VARCHAR(255),
    pid_revision VARCHAR(50),
    list_type VARCHAR(50) DEFAULT 'line_list',
    document_id VARCHAR(255),
    processed_by_id INTEGER REFERENCES core_customuser(id) ON DELETE SET NULL,
    processing_date TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    excel_filename VARCHAR(500),
    excel_file VARCHAR(100),
    file_size INTEGER DEFAULT 0,
    total_lines INTEGER DEFAULT 0,
    total_columns INTEGER DEFAULT 0,
    processing_time_seconds DECIMAL(10, 2) DEFAULT 0,
    format_type VARCHAR(50) DEFAULT 'general',
    include_area BOOLEAN DEFAULT FALSE,
    enrichment_enabled BOOLEAN DEFAULT FALSE,
    data JSONB,
    notes TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_designiq_processed_outputs_pid ON designiq_processed_pid_outputs(pid_number);
CREATE INDEX IF NOT EXISTS idx_designiq_processed_outputs_list_type ON designiq_processed_pid_outputs(list_type);
CREATE INDEX IF NOT EXISTS idx_designiq_processed_outputs_date ON designiq_processed_pid_outputs(processing_date);
CREATE INDEX IF NOT EXISTS idx_designiq_processed_outputs_project ON designiq_processed_pid_outputs(project_id);
"""

with connection.cursor() as cursor:
    cursor.execute(create_table_sql)
    print("✓ Created designiq_processed_pid_outputs table")
    print("✓ Created indexes")
    
    # Check table exists
    cursor.execute("""
        SELECT COUNT(*) FROM information_schema.tables 
        WHERE table_name = 'designiq_processed_pid_outputs'
    """)
    result = cursor.fetchone()
    print(f"✓ Table exists: {result[0] > 0}")
    
print("✓ ProcessedPIDOutput table created successfully!")
