"""
Database Setup Helper & Password Change Utility
This script helps you change passwords even without a full database setup
"""
import os
import sys

print("""
================================================================================
🔐 SMART PASSWORD CHANGE UTILITY
================================================================================

This utility will help you change a user's password.

IMPORTANT: You need database access to change passwords.

OPTIONS:
--------
1. Use Production Database (Railway)
   - Requires valid DATABASE_URL or DB credentials
   - Changes password in production database
   
2. Use Local Database
   - Requires local PostgreSQL running
   - Changes password in local database
   
3. Setup Local SQLite (Quick Test)
   - No PostgreSQL needed
   - For testing only

================================================================================
""")

def create_env_file_production():
    """Create .env file for production database"""
    print("\n📝 Creating .env file for PRODUCTION database...")
    print("\nPlease provide the following information:")
    
    db_password = input("\nEnter PostgreSQL password (or press Enter to skip): ").strip()
    
    if not db_password:
        print("\n⚠️  Skipping .env creation. You'll need to set up the database manually.")
        return False
    
    db_host = input("Enter DB Host (default: shinkansen.proxy.rlwy.net): ").strip() or "shinkansen.proxy.rlwy.net"
    db_port = input("Enter DB Port (default: 38534): ").strip() or "38534"
    db_name = input("Enter DB Name (default: railway): ").strip() or "railway"
    db_user = input("Enter DB User (default: postgres): ").strip() or "postgres"
    
    env_content = f"""# Production Database Configuration
DATABASE_URL=postgresql://{db_user}:{db_password}@{db_host}:{db_port}/{db_name}

# Django Configuration
SECRET_KEY=django-insecure-temporary-key-change-in-production
DEBUG=True
ALLOWED_HOSTS=*

# Disable S3 for password changes
USE_S3=False
S3_READY=False
"""
    
    with open('.env', 'w') as f:
        f.write(env_content)
    
    print("\n✅ .env file created successfully!")
    return True

def create_env_file_local():
    """Create .env file for local database"""
    print("\n📝 Creating .env file for LOCAL database...")
    
    db_password = input("\nEnter local PostgreSQL password (default: postgres): ").strip() or "postgres"
    db_name = input("Enter DB Name (default: radai_db): ").strip() or "radai_db"
    
    env_content = f"""# Local Database Configuration
DB_NAME={db_name}
DB_USER=postgres
DB_PASSWORD={db_password}
DB_HOST=localhost
DB_PORT=5432

# Django Configuration
SECRET_KEY=django-insecure-temporary-key-for-local-dev
DEBUG=True
ALLOWED_HOSTS=localhost,127.0.0.1

# Disable S3 for password changes
USE_S3=False
S3_READY=False
"""
    
    with open('.env', 'w') as f:
        f.write(env_content)
    
    print("\n✅ .env file created successfully!")
    return True

def create_env_file_sqlite():
    """Create .env file for SQLite"""
    print("\n📝 Creating .env file for SQLITE (testing only)...")
    
    env_content = """# SQLite Configuration (Testing Only)
DEBUG=True
ALLOWED_HOSTS=*
SECRET_KEY=django-insecure-temporary-key

# No DATABASE_URL means Django will use SQLite
# USE_S3=False
# S3_READY=False
"""
    
    with open('.env', 'w') as f:
        f.write(env_content)
    
    print("\n✅ .env file created for SQLite!")
    print("\n⚠️  NOTE: You'll need to run migrations first:")
    print("   python manage.py migrate")
    return True

def change_password_interactive():
    """Interactive password change"""
    print("\n" + "=" * 80)
    print("📝 PASSWORD CHANGE DETAILS")
    print("=" * 80)
    
    email = input("\nEnter user email: ").strip()
    
    if not email:
        print("❌ Email is required!")
        return
    
    from getpass import getpass
    password = getpass("Enter new password: ")
    password_confirm = getpass("Confirm new password: ")
    
    if password != password_confirm:
        print("❌ Passwords do not match!")
        return
    
    must_reset = input("\nUser must reset password on login? (y/N): ").strip().lower() == 'y'
    is_temp = input("Mark as temporary password? (y/N): ").strip().lower() == 'y'
    
    print("\n" + "=" * 80)
    print("🚀 EXECUTING PASSWORD CHANGE")
    print("=" * 80)
    
    # Build command
    cmd = f'python manage.py change_password --email "{email}" --password "{password}"'
    
    if must_reset:
        cmd += ' --must-reset'
    
    if is_temp:
        cmd += ' --temp-password'
    
    print(f"\nRunning: python manage.py change_password --email {email} [...]")
    
    # Execute
    os.system(cmd)

def main():
    """Main menu"""
    
    # Check if .env exists
    env_exists = os.path.exists('.env')
    
    if env_exists:
        print("✅ .env file found!")
        use_existing = input("\nUse existing .env configuration? (Y/n): ").strip().lower()
        
        if use_existing != 'n':
            change_password_interactive()
            return
    
    # Show setup menu
    print("\n" + "=" * 80)
    print("DATABASE SETUP OPTIONS")
    print("=" * 80)
    print("\n1. Production Database (Railway)")
    print("2. Local PostgreSQL Database")
    print("3. SQLite (Testing Only)")
    print("4. Exit")
    
    choice = input("\nSelect option (1-4): ").strip()
    
    if choice == '1':
        if create_env_file_production():
            proceed = input("\nProceed with password change? (Y/n): ").strip().lower()
            if proceed != 'n':
                change_password_interactive()
    
    elif choice == '2':
        if create_env_file_local():
            proceed = input("\nProceed with password change? (Y/n): ").strip().lower()
            if proceed != 'n':
                change_password_interactive()
    
    elif choice == '3':
        if create_env_file_sqlite():
            print("\n⚠️  You need to run migrations first!")
            run_migrations = input("Run migrations now? (Y/n): ").strip().lower()
            if run_migrations != 'n':
                os.system('python manage.py migrate')
                print("\n")
                proceed = input("Migrations complete. Proceed with password change? (Y/n): ").strip().lower()
                if proceed != 'n':
                    change_password_interactive()
    
    elif choice == '4':
        print("\n👋 Goodbye!")
        sys.exit(0)
    
    else:
        print("\n❌ Invalid choice!")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Cancelled by user. Goodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
