"""
Quick MongoDB Setup Script
Run this after installing dependencies to initialize MongoDB
"""

import os
import sys
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.core.mongodb_client import mongodb_client
from django.conf import settings


def main():
    print("=" * 60)
    print("  MongoDB Setup for AIFlow")
    print("=" * 60)
    print()
    
    # 1. Test Connection
    print("📡 Testing MongoDB connection...")
    try:
        if mongodb_client.ping():
            print("✅ MongoDB connection successful!")
        else:
            print("❌ MongoDB connection failed!")
            return False
    except Exception as e:
        print(f"❌ Connection error: {str(e)}")
        print()
        print("💡 Make sure MongoDB URI is set in .env:")
        print(f"   MONGODB_URI={settings.MONGODB_URI}")
        return False
    
    print()
    
    # 2. Display Stats
    print("📊 Database Statistics:")
    try:
        stats = mongodb_client.get_stats()
        print(f"   Database: {stats.get('database', 'N/A')}")
        print(f"   Collections: {stats.get('collections', 0)}")
        print(f"   Documents: {stats.get('objects', 0)}")
        print(f"   Data Size: {stats.get('data_size', 0):,} bytes")
        print(f"   Storage Size: {stats.get('storage_size', 0):,} bytes")
    except Exception as e:
        print(f"   ⚠️  Could not fetch stats: {str(e)}")
    
    print()
    
    # 3. Create Indexes
    print("🔧 Creating database indexes...")
    try:
        mongodb_client.create_indexes()
        print("✅ Indexes created successfully!")
    except Exception as e:
        print(f"❌ Index creation failed: {str(e)}")
        return False
    
    print()
    
    # 4. Display Collection Info
    print("📦 Collections:")
    for alias, collection_name in settings.MONGODB_COLLECTIONS.items():
        print(f"   • {alias} → {collection_name}")
    
    print()
    print("=" * 60)
    print("  ✅ MongoDB Setup Complete!")
    print("=" * 60)
    print()
    print("Next steps:")
    print("1. Upload a P&ID drawing to test the integration")
    print("2. (Optional) Migrate existing reports:")
    print("   python manage.py migrate_to_mongodb")
    print()
    
    return True


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
