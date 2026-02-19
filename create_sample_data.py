#!/usr/bin/env python
"""
Create sample data for Smart Project Collection system
Run this script to populate the database with realistic project data
"""
import os
import sys
import django

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.users.models import User
from apps.core.models import ProjectCollection, ProjectDiscipline, SmartProjectDocument
from datetime import datetime, timedelta
import json

def create_sample_data():
    """Create comprehensive sample data for dashboard testing"""
    
    print("🚀 Creating sample data for Smart Project Collection system...")
    
    # Create sample users if they don't exist
    admin_user, created = User.objects.get_or_create(
        username='admin_user',
        defaults={
            'email': 'admin@radai.ae',
            'first_name': 'Admin',
            'last_name': 'User',
            'is_staff': True,
            'is_superuser': True
        }
    )
    if created:
        print(f"✅ Created admin user: {admin_user.username}")
    
    engineer_user, created = User.objects.get_or_create(
        username='lead_engineer',  
        defaults={
            'email': 'engineer@radai.ae',
            'first_name': 'Lead',
            'last_name': 'Engineer',
            'is_staff': False
        }
    )
    if created:
        print(f"✅ Created engineer user: {engineer_user.username}")
    
    # Create sample project collections
    projects_data = [
        {
            'project_code': 'RADAI-P001',
            'project_name': 'RADAI Process Engineering Hub',
            'client_name': 'Rejlers Engineering',
            'project_type': 'engineering',
            'project_status': 'active',
            'total_documents': 25,
            'total_size_bytes': 1024 * 1024 * 150,  # 150MB
            'discipline_count': 4,
            's3_root_path': 'projects/RADAI-P001/',
            'folder_structure': {
                'process': ['PFDs', 'PIDs', 'Datasheets'],
                'electrical': ['Circuit_Diagrams', 'Motor_Lists'],
                'mechanical': ['Equipment_Layouts', '3D_Models']
            },
            'auto_organize_enabled': True,
            'ai_classification_enabled': True,
            'cross_discipline_recommendations': True,
            'last_document_upload': datetime.now()
        },
        {
            'project_code': 'RADAI-P002',
            'project_name': 'Smart Manufacturing Plant',
            'client_name': 'Industrial Solutions Ltd',
            'project_type': 'engineering',
            'project_status': 'active',
            'total_documents': 42,
            'total_size_bytes': 1024 * 1024 * 320,  # 320MB
            'discipline_count': 6,
            's3_root_path': 'projects/RADAI-P002/',
            'folder_structure': {
                'process': ['Process_Flows', 'Equipment_Specs'],
                'electrical': ['Power_Distribution', 'Control_Systems'],
                'instrumentation': ['Loop_Diagrams', 'IO_Lists'],
                'safety': ['HAZOP_Studies', 'SIL_Assessments']
            },
            'auto_organize_enabled': True,
            'ai_classification_enabled': True,
            'cross_discipline_recommendations': True,
            'last_document_upload': datetime.now()
        },
        {
            'project_code': 'RADAI-P003',
            'project_name': 'Oil Refinery Modernization',
            'client_name': 'PetroTech Operations',
            'project_type': 'engineering',
            'project_status': 'completed',
            'total_documents': 78,
            'total_size_bytes': 1024 * 1024 * 650,  # 650MB
            'discipline_count': 8,
            's3_root_path': 'projects/RADAI-P003/',
            'folder_structure': {
                'process': ['PFDs', 'PIDs', 'Process_Calcs'],
                'mechanical': ['Equipment_Drawings', 'Vessel_Specs'],
                'electrical': ['Electrical_Drawings', 'Motor_Schedules'],
                'piping': ['Isometrics', 'Piping_Plans'],
                'instrumentation': ['Instrument_Specs', 'Cable_Schedules'],
                'civil': ['Structural_Drawings', 'Foundation_Plans'],
                'safety': ['Risk_Assessments', 'Safety_Reports'],
                'environmental': ['Environmental_Studies', 'Permits']
            },
            'auto_organize_enabled': True,
            'ai_classification_enabled': True,
            'cross_discipline_recommendations': True,
            'last_document_upload': datetime.now() - timedelta(days=30)  # 30 days ago
        },
        {
            'project_code': 'RADAI-P004',
            'project_name': 'Water Treatment Facility',
            'client_name': 'Municipal Water Authority',
            'project_type': 'engineering',
            'project_status': 'on_hold',
            'total_documents': 18,
            'total_size_bytes': 1024 * 1024 * 95,  # 95MB
            'discipline_count': 3,
            's3_root_path': 'projects/RADAI-P004/',
            'folder_structure': {
                'process': ['Treatment_Flows', 'Equipment_Lists'],
                'civil': ['Site_Plans', 'Structural_Designs'],
                'environmental': ['Impact_Studies', 'Permits']
            },
            'auto_organize_enabled': True,
            'ai_classification_enabled': True,
            'cross_discipline_recommendations': True,
            'last_document_upload': datetime.now() - timedelta(days=15)
        }
    ]
    
    created_projects = []
    for project_data in projects_data:
        project, created = ProjectCollection.objects.get_or_create(
            project_code=project_data['project_code'],
            defaults=project_data
        )
        created_projects.append(project)
        if created:
            print(f"✅ Created project: {project.project_code} - {project.project_name}")
    
    # Create sample disciplines for projects
    disciplines_data = [
        # RADAI-P001 disciplines
        (created_projects[0], 'Process Engineering', 'process', 8),
        (created_projects[0], 'Electrical Engineering', 'electrical', 6),
        (created_projects[0], 'Mechanical Engineering', 'mechanical', 7),
        (created_projects[0], 'Instrumentation & Control', 'instrumentation', 4),
        
        # RADAI-P002 disciplines
        (created_projects[1], 'Process Engineering', 'process', 12),
        (created_projects[1], 'Electrical Engineering', 'electrical', 10),
        (created_projects[1], 'Instrumentation & Control', 'instrumentation', 8),
        (created_projects[1], 'Safety & Risk', 'safety', 6),
        (created_projects[1], 'Civil & Structural', 'civil', 4),
        (created_projects[1], 'Environmental', 'environmental', 2),
        
        # RADAI-P003 disciplines
        (created_projects[2], 'Process Engineering', 'process', 18),
        (created_projects[2], 'Mechanical Engineering', 'mechanical', 15),
        (created_projects[2], 'Electrical Engineering', 'electrical', 12),
        (created_projects[2], 'Piping Engineering', 'piping', 14),
        (created_projects[2], 'Instrumentation & Control', 'instrumentation', 8),
        (created_projects[2], 'Civil & Structural', 'civil', 6),
        (created_projects[2], 'Safety & Risk', 'safety', 3),
        (created_projects[2], 'Environmental', 'environmental', 2),
        
        # RADAI-P004 disciplines
        (created_projects[3], 'Process Engineering', 'process', 10),
        (created_projects[3], 'Civil & Structural', 'civil', 5),
        (created_projects[3], 'Environmental', 'environmental', 3),
    ]
    
    discipline_count = 0
    for proj, name, dtype, doc_count in disciplines_data:
        discipline, created = ProjectDiscipline.objects.get_or_create(
            project=proj,
            discipline_name=name,
            defaults={
                'discipline_type': dtype,
                'document_count': doc_count,
                'size_bytes': doc_count * 1024 * 1024 * 2,  # Approx 2MB per document
                's3_discipline_path': f'{proj.s3_root_path}{dtype}/',
                'document_types': ['Technical Drawing', 'Specification', 'Calculation'],
                'lead_engineer': engineer_user if dtype in ['process', 'mechanical'] else admin_user
            }
        )
        if created:
            discipline_count += 1
    
    print(f"✅ Created {discipline_count} disciplines across all projects")
    
    # Update project statistics
    for project in created_projects:
        project.total_documents = project.disciplines.aggregate(
            total=django.db.models.Sum('document_count')
        )['total'] or 0
        project.discipline_count = project.disciplines.count()
        project.save()
    
    # Print summary statistics
    print("\n📊 DATABASE SUMMARY:")
    print(f"Total Users: {User.objects.count()}")
    print(f"Total Projects: {ProjectCollection.objects.count()}")
    print(f"Active Projects: {ProjectCollection.objects.filter(project_status='active').count()}")
    print(f"Completed Projects: {ProjectCollection.objects.filter(project_status='completed').count()}")
    print(f"On Hold Projects: {ProjectCollection.objects.filter(project_status='on_hold').count()}")
    print(f"Total Disciplines: {ProjectDiscipline.objects.count()}")
    
    # Calculate total documents and storage
    total_docs = ProjectCollection.objects.aggregate(
        total=django.db.models.Sum('total_documents')
    )['total'] or 0
    total_bytes = ProjectCollection.objects.aggregate(
        total=django.db.models.Sum('total_size_bytes')
    )['total'] or 0
    total_gb = round(total_bytes / (1024 * 1024 * 1024), 2)
    
    print(f"Total Documents: {total_docs}")
    print(f"Total Storage: {total_gb} GB")
    
    print("\n✅ Sample data creation completed successfully!")
    return True

if __name__ == '__main__':
    import django.db.models
    try:
        create_sample_data()
    except Exception as e:
        print(f"❌ Error creating sample data: {e}")
        sys.exit(1)