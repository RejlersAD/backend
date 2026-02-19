#!/usr/bin/env python
"""
Railway Database Configuration Update
Updates local environment to connect to Railway production database
"""
import os
import sys

def update_database_config(railway_url):
    """Update Docker Compose and Django settings to use Railway database"""
    
    print('🔧 UPDATING DATABASE CONFIGURATION FOR RAILWAY')
    print('=' * 55)
    
    # Extract components from Railway URL
    # Format: postgresql://username:password@host:port/database
    if not railway_url.startswith('postgresql://'):
        print('❌ Invalid PostgreSQL URL format')
        return False
    
    # Parse the URL
    url_parts = railway_url.replace('postgresql://', '').split('@')
    if len(url_parts) != 2:
        print('❌ Could not parse database URL')
        return False
    
    user_pass = url_parts[0].split(':')
    host_port_db = url_parts[1].split('/')
    
    if len(user_pass) != 2 or len(host_port_db) != 2:
        print('❌ Invalid URL format')
        return False
    
    username = user_pass[0]
    password = user_pass[1]
    host_port = host_port_db[0].split(':')
    database = host_port_db[1]
    
    if len(host_port) != 2:
        print('❌ Invalid host:port format')
        return False
    
    host = host_port[0]
    port = host_port[1]
    
    print(f'📊 DATABASE CONNECTION DETAILS:')
    print(f'   Host: {host}')
    print(f'   Port: {port}')
    print(f'   Database: {database}')
    print(f'   Username: {username}')
    print(f'   Password: {"*" * len(password)}')
    
    # Update environment variables for Docker Compose
    env_content = f'''# Railway PostgreSQL Database Configuration
DATABASE_URL={railway_url}
DB_HOST={host}
DB_PORT={port}
DB_NAME={database}
DB_USER={username}
DB_PASSWORD={password}

# Keep other existing environment variables
SECRET_KEY=your-secret-key-here
AWS_ACCESS_KEY_ID=your-aws-access-key
AWS_SECRET_ACCESS_KEY=your-aws-secret-key
AWS_STORAGE_BUCKET_NAME=rejlers-engineering-data
OPENAI_API_KEY=your-openai-api-key
'''
    
    # Write to .env file
    env_file_path = '.env'
    with open(env_file_path, 'w') as f:
        f.write(env_content)
    
    print(f'✅ Created {env_file_path} with Railway database configuration')
    
    # Instructions for docker-compose update
    docker_compose_update = '''
# ADD TO docker-compose.yml backend service environment:
environment:
  - DATABASE_URL=${DATABASE_URL}
  - DB_HOST=${DB_HOST}
  - DB_PORT=${DB_PORT}
  - DB_NAME=${DB_NAME}
  - DB_USER=${DB_USER}
  - DB_PASSWORD=${DB_PASSWORD}
'''
    
    print(f'\\n📋 DOCKER COMPOSE UPDATE NEEDED:')
    print(docker_compose_update)
    
    print(f'\\n🚀 NEXT STEPS:')
    print('1. Update docker-compose.yml with the environment variables above')
    print('2. Restart containers: docker-compose restart backend_local')
    print('3. Test connection: docker-compose exec backend_local python manage.py dbshell')
    print('4. Check user count: docker-compose exec backend_local python manage.py shell -c "from apps.users.models import User; print(f\'Users: {User.objects.count()}\')"')
    
    return True

def test_railway_connection(railway_url):
    """Test connection to Railway database"""
    try:
        import psycopg2
        conn = psycopg2.connect(railway_url)
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) FROM users_user')
        user_count = cursor.fetchone()[0]
        cursor.close()
        conn.close()
        print(f'✅ Connection successful! Found {user_count} users.')
        return user_count
    except Exception as e:
        print(f'❌ Connection test failed: {e}')
        return None

if __name__ == '__main__':
    print('🔗 RAILWAY DATABASE SETUP TOOL')
    print('=' * 40)
    
    # Get Railway URL from user
    if len(sys.argv) > 1:
        railway_url = sys.argv[1]
    else:
        railway_url = input('🔗 Enter your Railway database URL: ').strip()
    
    if not railway_url:
        print('❌ No database URL provided')
        sys.exit(1)
    
    # Test connection first
    print('🧪 Testing connection...')
    user_count = test_railway_connection(railway_url)
    
    if user_count:
        print(f'🎉 SUCCESS! Railway database has {user_count} users')
        
        # Update configuration
        if update_database_config(railway_url):
            print('\\n✅ Configuration updated successfully!')
            print('🔄 Now restart your containers to connect to Railway database.')
        else:
            print('❌ Failed to update configuration')
    else:
        print('⚠️  Could not connect to Railway database')
        print('Please verify the URL and try again.')