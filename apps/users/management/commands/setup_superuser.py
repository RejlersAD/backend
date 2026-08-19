"""
Django management command to create or update superuser
"""
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from django.db import connection

User = get_user_model()


class Command(BaseCommand):
    help = 'Create or update superuser with specific credentials'

    def add_arguments(self, parser):
        parser.add_argument('--email', type=str, help='Email address')
        parser.add_argument('--username', type=str, help='Username')
        parser.add_argument('--password', type=str, help='Password')

    def handle(self, *args, **options):
        # Test database connection
        self.stdout.write(self.style.SUCCESS('Testing database connection...'))
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            self.stdout.write(self.style.SUCCESS('✓ Database connection successful'))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'✗ Database connection failed: {str(e)}'))
            return

        # Get credentials from options or use defaults
        email = options.get('email') or 'tanzeem.agra@rejlers.ae'
        username = options.get('username') or 'tanzeem'
        password = options.get('password') or 'Tanzeem@123'

        self.stdout.write(f'\nChecking for user: {email}')

        try:
            # Check if user exists
            user = User.objects.filter(email=email).first()
            
            if user:
                self.stdout.write(self.style.WARNING(f'User {email} already exists'))
                self.stdout.write(f'  - Username: {user.username}')
                self.stdout.write(f'  - Is superuser: {user.is_superuser}')
                self.stdout.write(f'  - Is staff: {user.is_staff}')
                self.stdout.write(f'  - Is active: {user.is_active}')
                
                # Update password and make superuser
                user.set_password(password)
                user.is_superuser = True
                user.is_staff = True
                user.is_active = True
                user.is_verified = True
                user.save()
                self.stdout.write(self.style.SUCCESS(f'\n✓ Updated user {email}'))
                self.stdout.write(self.style.SUCCESS('  - Password updated'))
                self.stdout.write(self.style.SUCCESS('  - Made superuser'))
                self.stdout.write(self.style.SUCCESS('  - Activated account'))
            else:
                # Create new superuser
                user = User.objects.create_superuser(
                    email=email,
                    username=username,
                    password=password
                )
                user.is_verified = True
                user.save()
                self.stdout.write(self.style.SUCCESS(f'\n✓ Created superuser {email}'))
                self.stdout.write(self.style.SUCCESS(f'  - Username: {username}'))
                self.stdout.write(self.style.SUCCESS(f'  - Password: {password}'))

            # List all users
            self.stdout.write('\n' + '='*50)
            self.stdout.write('All users in database:')
            self.stdout.write('='*50)
            for u in User.objects.all().order_by('-date_joined'):
                status = '✓' if u.is_active else '✗'
                super_status = '[SUPER]' if u.is_superuser else '[USER]'
                self.stdout.write(f'{status} {super_status} {u.email} ({u.username})')
            
            self.stdout.write('\n' + self.style.SUCCESS('='*50))
            self.stdout.write(self.style.SUCCESS('Login credentials:'))
            self.stdout.write(self.style.SUCCESS('='*50))
            self.stdout.write(self.style.SUCCESS(f'Email: {email}'))
            self.stdout.write(self.style.SUCCESS(f'Password: {password}'))
            self.stdout.write(self.style.SUCCESS('='*50))

        except Exception as e:
            self.stdout.write(self.style.ERROR(f'\n✗ Error: {str(e)}'))
            import traceback
            traceback.print_exc()
