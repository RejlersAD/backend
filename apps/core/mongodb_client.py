"""
MongoDB Connection Manager for AIFlow
Smart singleton pattern for MongoDB connections with connection pooling
"""

import logging
from typing import Optional, Dict, Any
from pymongo import MongoClient
from pymongo.database import Database
from pymongo.collection import Collection
from django.conf import settings

logger = logging.getLogger(__name__)


class MongoDBClient:
    """
    Singleton MongoDB client for AIFlow
    Manages connection pooling and provides database/collection access
    """
    
    _instance: Optional['MongoDBClient'] = None
    _client: Optional[MongoClient] = None
    _database: Optional[Database] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize MongoDB client (only once due to singleton)"""
        if self._client is None:
            self._connect()
    
    def _connect(self):
        """Establish MongoDB connection with smart configuration"""
        try:
            # Get connection settings from Django settings
            uri = settings.MONGODB_URI
            database_name = settings.MONGODB_DATABASE
            options = settings.MONGODB_OPTIONS.copy()
            
            # Add directConnection for Railway MongoDB
            options['directConnection'] = True
            
            # Add server selection timeout to connection options
            options['serverSelectionTimeoutMS'] = 30000
            
            # Create MongoDB client with connection pooling
            self._client = MongoClient(uri, **options)
            
            # Get database
            self._database = self._client[database_name]
            
            # Test connection
            self._client.admin.command('ping')
            
            logger.info(f"✅ MongoDB connected successfully to database: {database_name}")
            logger.info(f"📊 Connection pool size: {options.get('maxPoolSize', 'default')}")
            
        except Exception as e:
            logger.warning(f"⚠️ MongoDB connection failed: {str(e)}")
            logger.warning("MongoDB will be initialized on first use")
            # Don't raise - allow system to continue without MongoDB
            self._client = None
            self._database = None
    
    def get_database(self) -> Database:
        """Get the MongoDB database instance"""
        if self._database is None or self._client is None:
            self._connect()
        
        if self._database is None:
            raise ConnectionError("MongoDB is not connected. Check your MONGODB_URI setting.")
        
        return self._database
    
    def get_collection(self, collection_name: str) -> Collection:
        """
        Get a collection by name
        
        Args:
            collection_name: Name of the collection or alias from settings
        
        Returns:
            MongoDB Collection instance
        """
        # Check if it's an alias
        actual_name = settings.MONGODB_COLLECTIONS.get(collection_name, collection_name)
        
        db = self.get_database()
        return db[actual_name]
    
    def close(self):
        """Close MongoDB connection"""
        if self._client:
            self._client.close()
            logger.info("MongoDB connection closed")
            self._client = None
            self._database = None
    
    def ping(self) -> bool:
        """
        Check if MongoDB connection is alive
        
        Returns:
            True if connection is alive, False otherwise
        """
        try:
            self._client.admin.command('ping')
            return True
        except Exception as e:
            logger.error(f"MongoDB ping failed: {str(e)}")
            return False
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get MongoDB connection and database statistics
        
        Returns:
            Dictionary with connection stats
        """
        try:
            db = self.get_database()
            stats = db.command('dbStats')
            
            return {
                'database': settings.MONGODB_DATABASE,
                'collections': stats.get('collections', 0),
                'data_size': stats.get('dataSize', 0),
                'storage_size': stats.get('storageSize', 0),
                'indexes': stats.get('indexes', 0),
                'objects': stats.get('objects', 0),
            }
        except Exception as e:
            logger.error(f"Failed to get MongoDB stats: {str(e)}")
            return {}
    
    def create_indexes(self):
        """Create indexes for better query performance"""
        try:
            # PID Reports collection indexes
            reports_collection = self.get_collection('pid_reports')
            reports_collection.create_index('drawing_id')
            reports_collection.create_index('user_id')
            reports_collection.create_index('created_at')
            reports_collection.create_index([('created_at', -1)])  # Descending for latest first
            
            # PID Issues collection indexes
            issues_collection = self.get_collection('pid_issues')
            issues_collection.create_index('report_id')
            issues_collection.create_index('status')
            issues_collection.create_index('severity')
            issues_collection.create_index([('report_id', 1), ('serial_number', 1)])
            
            # Reference Documents collection indexes
            ref_docs_collection = self.get_collection('reference_docs')
            ref_docs_collection.create_index('category')
            ref_docs_collection.create_index('is_active')
            ref_docs_collection.create_index('embedding_status')
            ref_docs_collection.create_index([('category', 1), ('is_active', 1)])
            
            # Analysis Logs collection indexes
            logs_collection = self.get_collection('analysis_logs')
            logs_collection.create_index('drawing_id')
            logs_collection.create_index('timestamp')
            logs_collection.create_index([('timestamp', -1)])
            
            logger.info("✅ MongoDB indexes created successfully")
            
        except Exception as e:
            logger.error(f"❌ Failed to create MongoDB indexes: {str(e)}")


# Global MongoDB client instance
mongodb_client = MongoDBClient()


# Convenience functions for easy access
def get_mongodb_database() -> Database:
    """Get MongoDB database instance"""
    return mongodb_client.get_database()


def get_mongodb_collection(collection_name: str) -> Collection:
    """Get MongoDB collection by name or alias"""
    return mongodb_client.get_collection(collection_name)


def close_mongodb_connection():
    """Close MongoDB connection"""
    mongodb_client.close()
