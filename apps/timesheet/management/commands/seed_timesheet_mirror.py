"""
Seed TimesheetEvent mirror table with sample data for production testing.

This command helps bootstrap production when the office-side sync agent
(timesheet_mirror_sync.py) isn't set up yet. It creates realistic attendance
data so HR/Employees page works immediately.

Usage:
    python manage.py seed_timesheet_mirror
    python manage.py seed_timesheet_mirror --days 30
    python manage.py seed_timesheet_mirror --employees 50 --days 14
    railway run python manage.py seed_timesheet_mirror --days 7
    
Options:
    --days N        Number of days of history to generate (default: 7)
    --employees N   Number of employees to generate (default: 20)
    --clear         Clear existing events before seeding
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import datetime, timedelta
import hashlib
import random

from apps.timesheet.models import TimesheetEvent, BiometricUserMaster


class Command(BaseCommand):
    help = 'Seed timesheet mirror table with sample attendance data'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=7,
            help='Number of days of history to generate (default: 7)',
        )
        parser.add_argument(
            '--employees',
            type=int,
            default=20,
            help='Number of employees to generate (default: 20)',
        )
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Clear existing events before seeding',
        )

    def handle(self, *args, **options):
        days = options['days']
        num_employees = options['employees']
        clear = options['clear']

        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS("🌱 TIMESHEET MIRROR SEEDER"))
        self.stdout.write("=" * 80)
        self.stdout.write(f"\nConfiguration:")
        self.stdout.write(f"  • Days of history: {days}")
        self.stdout.write(f"  • Number of employees: {num_employees}")
        self.stdout.write(f"  • Clear existing data: {clear}")
        self.stdout.write("")

        # Clear existing data if requested
        if clear:
            count = TimesheetEvent.objects.count()
            if count > 0:
                self.stdout.write(f"⚠️  Clearing {count:,} existing events...")
                TimesheetEvent.objects.all().delete()
                self.stdout.write(self.style.SUCCESS("✅ Cleared"))

        # Generate employee data
        self.stdout.write("\n📋 Generating employee master data...")
        employees = self._generate_employees(num_employees)
        self.stdout.write(self.style.SUCCESS(f"✅ Generated {len(employees)} employees"))

        # Generate attendance events
        self.stdout.write("\n📅 Generating attendance events...")
        events_created = self._generate_events(employees, days)
        self.stdout.write(self.style.SUCCESS(f"✅ Created {events_created:,} timesheet events"))

        # Show summary
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS("✅ SEEDING COMPLETE"))
        self.stdout.write("=" * 80)
        
        total_events = TimesheetEvent.objects.count()
        latest_event = TimesheetEvent.objects.order_by('-event_time').first()
        
        self.stdout.write(f"\n📊 Database Summary:")
        self.stdout.write(f"  • Total events: {total_events:,}")
        self.stdout.write(f"  • Latest event: {latest_event.event_time if latest_event else 'None'}")
        self.stdout.write(f"  • Unique employees: {TimesheetEvent.objects.values('employee_code').distinct().count()}")
        
        self.stdout.write(f"\n🎯 Next Steps:")
        self.stdout.write(f"  1. Visit: https://www.radai.ae/hr/employees")
        self.stdout.write(f"  2. The timesheet Live/Daily/Monthly tabs should now show data")
        self.stdout.write(f"  3. Set up the real sync agent (timesheet_mirror_sync.py) for production use")
        self.stdout.write("")

    def _generate_employees(self, count):
        """Generate sample employee data and seed BiometricUserMaster"""
        departments = ['Engineering', 'Finance', 'HR', 'Operations', 'IT', 'Admin']
        employees = []
        
        for i in range(1, count + 1):
            employee_code = f"{1000 + i}"
            full_name = f"Employee {i:02d}"
            first_name = f"Employee"
            last_name = f"{i:02d}"
            email = f"employee{i:02d}@rejlers.com"
            department = random.choice(departments)
            
            # Upsert BiometricUserMaster
            BiometricUserMaster.objects.update_or_create(
                employee_code=employee_code,
                defaults={
                    'full_name': full_name,
                    'first_name': first_name,
                    'last_name': last_name,
                    'office_email': email,
                    'personal_email': email,
                    'card1': employee_code,
                    'department': department,
                    'active': True,
                }
            )
            
            employees.append({
                'code': employee_code,
                'name': full_name,
                'email': email,
                'department': department,
            })
        
        return employees

    def _generate_events(self, employees, days):
        """Generate realistic punch-in/out events"""
        events_created = 0
        now = timezone.now()
        
        # Generate events for each day (going backwards)
        for day_offset in range(days):
            date = (now - timedelta(days=day_offset)).date()
            
            # Skip weekends (simplified)
            if date.weekday() >= 5:  # Saturday = 5, Sunday = 6
                continue
            
            # Each employee has ~80% attendance
            for emp in employees:
                if random.random() > 0.8:  # 20% absence rate
                    continue
                
                # Generate IN event (between 7:00 and 10:00)
                in_hour = random.randint(7, 9)
                in_minute = random.randint(0, 59)
                in_time = timezone.make_aware(
                    datetime.combine(date, datetime.min.time().replace(
                        hour=in_hour, minute=in_minute
                    ))
                )
                
                # Generate OUT event (between 16:00 and 19:00)
                out_hour = random.randint(16, 18)
                out_minute = random.randint(0, 59)
                out_time = timezone.make_aware(
                    datetime.combine(date, datetime.min.time().replace(
                        hour=out_hour, minute=out_minute
                    ))
                )
                
                # Create IN event
                in_event_id = self._generate_event_id(emp['code'], in_time, 'IN')
                TimesheetEvent.objects.update_or_create(
                    source_event_id=in_event_id,
                    defaults={
                        'employee_code': emp['code'],
                        'employee_name': emp['name'],
                        'employee_email': emp['email'],
                        'department': emp['department'],
                        'event_time': in_time,
                        'event_type': 'IN',
                    }
                )
                events_created += 1
                
                # Create OUT event
                out_event_id = self._generate_event_id(emp['code'], out_time, 'OUT')
                TimesheetEvent.objects.update_or_create(
                    source_event_id=out_event_id,
                    defaults={
                        'employee_code': emp['code'],
                        'employee_name': emp['name'],
                        'employee_email': emp['email'],
                        'department': emp['department'],
                        'event_time': out_time,
                        'event_type': 'OUT',
                    }
                )
                events_created += 1
        
        return events_created

    def _generate_event_id(self, employee_code, event_time, event_type):
        """Generate deterministic event ID (same as sync agent)"""
        key = f"{employee_code}|{event_time.isoformat()}|{event_type}"
        return hashlib.sha256(key.encode()).hexdigest()[:64]
