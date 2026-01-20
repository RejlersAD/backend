"""
Test MongoDB Connection
Simple script to test the MongoDB connection string
"""

from pymongo import MongoClient
import sys

# MongoDB URI from Railway
uri = "mongodb://mongo:kpdwrBybTiIyKRoUJmOAMfewZKtWqTeu@shuttle.proxy.rlwy.net:23002"

print("Testing MongoDB Connection...")
print(f"URI: {uri[:30]}...")
print()

try:
    # Try with different connection options
    print("Attempt 1: Basic connection...")
    client = MongoClient(
        uri,
        serverSelectionTimeoutMS=30000,
        connectTimeoutMS=30000,
        directConnection=True
    )
    
    # Test ping
    client.admin.command('ping')
    print("✅ Connection successful!")
    
    # Get database info
    db = client['aiflow']
    print(f"✅ Database 'aiflow' accessed")
    
    # List collections
    collections = db.list_collection_names()
    print(f"✅ Collections: {collections if collections else 'None (empty database)'}")
    
    # Get stats
    stats = db.command('dbStats')
    print(f"✅ Data size: {stats.get('dataSize', 0):,} bytes")
    print(f"✅ Collections count: {stats.get('collections', 0)}")
    
    client.close()
    print()
    print("🎉 MongoDB is working correctly!")
    
except Exception as e:
    print(f"❌ Connection failed: {type(e).__name__}")
    print(f"   Error: {str(e)}")
    print()
    print("Possible issues:")
    print("1. Railway MongoDB service might be stopped")
    print("2. Network/firewall blocking connection")
    print("3. Credentials may have changed")
    print("4. Check Railway dashboard for service status")
    sys.exit(1)
