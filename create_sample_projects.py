"""
Create sample projects for testing
"""
import os
import sys
import django
from datetime import date, timedelta

# Setup Django
sys.path.append('/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model
from apps.core.project_models import Project, ProjectMember, ProjectTask, ProjectMilestone

User = get_user_model()

def create_sample_projects():
    print("Creating sample projects...")
    
    # Get or create a user
    try:
        user = User.objects.filter(is_active=True).first()
        if not user:
            print("No active users found. Please create a user first.")
            return
        
        print(f"Using user: {user.email}")
        
        # Sample projects
        projects_data = [
            {
                'name': 'Oil Refinery Modernization',
                'code': 'PRJ-2024-001',
                'description': 'Complete modernization of the refinery processing units with new safety systems',
                'status': 'active',
                'priority': 'high',
                'progress': 45,
                'start_date': date.today() - timedelta(days=60),
                'end_date': date.today() + timedelta(days=90),
                'client_name': 'Arabian Oil Corporation',
                'location': 'Abu Dhabi, UAE',
                'budget': 5000000,
                'spent': 2250000,
            },
            {
                'name': 'Water Treatment Plant Expansion',
                'code': 'PRJ-2024-002',
                'description': 'Expansion of water treatment capacity by 50% with new filtration systems',
                'status': 'planning',
                'priority': 'medium',
                'progress': 15,
                'start_date': date.today() + timedelta(days=15),
                'end_date': date.today() + timedelta(days=180),
                'client_name': 'Dubai Water Authority',
                'location': 'Dubai, UAE',
                'budget': 3200000,
                'spent': 480000,
            },
            {
                'name': 'Gas Processing Unit Upgrade',
                'code': 'PRJ-2024-003',
                'description': 'Upgrade of gas processing units to increase efficiency and reduce emissions',
                'status': 'active',
                'priority': 'critical',
                'progress': 72,
                'start_date': date.today() - timedelta(days=120),
                'end_date': date.today() + timedelta(days=30),
                'client_name': 'Emirates Gas Company',
                'location': 'Sharjah, UAE',
                'budget': 4500000,
                'spent': 3240000,
            },
            {
                'name': 'Chemical Plant Safety Systems',
                'code': 'PRJ-2024-004',
                'description': 'Installation of advanced safety systems including fire detection and suppression',
                'status': 'on_hold',
                'priority': 'high',
                'progress': 28,
                'start_date': date.today() - timedelta(days=45),
                'end_date': date.today() + timedelta(days=120),
                'client_name': 'Gulf Chemicals Ltd',
                'location': 'Ras Al Khaimah, UAE',
                'budget': 2800000,
                'spent': 784000,
            },
            {
                'name': 'Power Plant Instrumentation',
                'code': 'PRJ-2024-005',
                'description': 'Complete instrumentation and control system upgrade for thermal power plant',
                'status': 'completed',
                'priority': 'medium',
                'progress': 100,
                'start_date': date.today() - timedelta(days=180),
                'end_date': date.today() - timedelta(days=10),
                'client_name': 'National Power Corporation',
                'location': 'Fujairah, UAE',
                'budget': 6500000,
                'spent': 6350000,
            },
        ]
        
        created_projects = []
        for proj_data in projects_data:
            project, created = Project.objects.get_or_create(
                code=proj_data['code'],
                defaults={**proj_data, 'owner': user}
            )
            
            if created:
                print(f"✅ Created project: {project.code} - {project.name}")
                
                # Add owner as project manager
                ProjectMember.objects.get_or_create(
                    project=project,
                    user=user,
                    defaults={'role': 'project_manager'}
                )
                
                # Create sample tasks
                if project.status in ['active', 'planning']:
                    task_data = [
                        {
                            'title': 'Complete preliminary design',
                            'status': 'completed' if project.progress > 30 else 'in_progress',
                            'priority': 'high',
                            'estimated_hours': 120,
                            'actual_hours': 115,
                        },
                        {
                            'title': 'Obtain regulatory approvals',
                            'status': 'in_progress' if project.progress > 20 else 'todo',
                            'priority': 'critical',
                            'estimated_hours': 80,
                            'actual_hours': 45,
                        },
                        {
                            'title': 'Procure equipment',
                            'status': 'todo',
                            'priority': 'medium',
                            'estimated_hours': 60,
                            'actual_hours': 0,
                        },
                    ]
                    
                    for task in task_data:
                        ProjectTask.objects.create(
                            project=project,
                            assigned_to=user,
                            due_date=project.end_date - timedelta(days=30),
                            **task
                        )
                
                # Create milestones
                milestone_data = [
                    {
                        'name': 'Design Approval',
                        'target_date': project.start_date + timedelta(days=30),
                        'is_completed': project.progress > 30,
                    },
                    {
                        'name': 'Equipment Delivery',
                        'target_date': project.start_date + timedelta(days=90),
                        'is_completed': project.progress > 60,
                    },
                    {
                        'name': 'Installation Complete',
                        'target_date': project.end_date - timedelta(days=15),
                        'is_completed': project.progress == 100,
                    },
                ]
                
                for milestone in milestone_data:
                    ms = ProjectMilestone.objects.create(
                        project=project,
                        **milestone
                    )
                    if ms.is_completed:
                        ms.completed_date = ms.target_date
                        ms.save()
                
                created_projects.append(project)
            else:
                print(f"⚠️  Project already exists: {project.code}")
        
        print(f"\n✅ Created {len(created_projects)} sample projects")
        print("\nProject Summary:")
        for project in Project.objects.all().order_by('code'):
            print(f"  • {project.code}: {project.name} [{project.status}] - {project.progress}% complete")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    create_sample_projects()
