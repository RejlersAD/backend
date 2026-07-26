"""Check Phase 2 project data"""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.pid_verification.models import PIDVProject, PIDVReferenceData, PIDVDocument

project = PIDVProject.objects.filter(project_name='Phase 2').first()
if project:
    print(f'Project: {project.project_name} (ID: {project.project_id})')
    print(f'  Created: {project.created_at}')
    print(f'  Legend Knowledge: {"Yes" if project.legend_knowledge_data else "No"}')
    
    ref_data = PIDVReferenceData.objects.filter(project=project)
    print(f'\nReference Data: {ref_data.count()} file(s)')
    for rd in ref_data:
        print(f'  - Type: {rd.data_type}, Status: {rd.status}, File: {rd.file_name}')
    
    docs = PIDVDocument.objects.filter(project=project)
    print(f'\nP&ID Documents: {docs.count()} file(s)')
    for doc in docs:
        print(f'  - File: {doc.document_title}, Status: {doc.status}')
else:
    print('Project not found')
