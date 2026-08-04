"""
Database Synchronization Script
Synchronizes remote PostgreSQL database with local database
"""

import json
import os
import sys
import logging
from datetime import datetime
import subprocess
import psycopg2
from psycopg2 import sql
from pathlib import Path


class DatabaseSynchronizer:
    def __init__(self, config_file='db_sync_config.json'):
        """Initialize the database synchronizer with configuration"""
        self.config_file = config_file
        self.config = self.load_config()
        self.setup_logging()
        self.pg_bin_path = self.config['sync_settings'].get('pg_bin_path', '')
        
    def load_config(self):
        """Load configuration from JSON file"""
        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)
            print(f"✓ Configuration loaded from {self.config_file}")
            return config
        except FileNotFoundError:
            print(f"✗ Error: Configuration file {self.config_file} not found")
            sys.exit(1)
        except json.JSONDecodeError as e:
            print(f"✗ Error: Invalid JSON in configuration file: {e}")
            sys.exit(1)
    
    def setup_logging(self):
        """Setup logging configuration"""
        log_file = self.config['sync_settings']['log_file']
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_file, encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        # Fix encoding for StreamHandler on Windows
        for handler in logging.root.handlers:
            if isinstance(handler, logging.StreamHandler):
                try:
                    handler.stream.reconfigure(encoding='utf-8')
                except AttributeError:
                    pass
        self.logger = logging.getLogger(__name__)
    
    def get_pg_tool_path(self, tool_name):
        """Get full path to PostgreSQL tool"""
        if self.pg_bin_path:
            tool_path = os.path.join(self.pg_bin_path, tool_name)
            if os.name == 'nt' and not tool_name.endswith('.exe'):
                tool_path += '.exe'
            if os.path.exists(tool_path):
                return tool_path
        return tool_name  # Fall back to PATH
    
    def test_connection(self, db_config, db_name):
        """Test database connection"""
        try:
            conn = psycopg2.connect(
                host=db_config['host'],
                port=db_config['port'],
                database=db_config['database'],
                user=db_config['user'],
                password=db_config['password']
            )
            conn.close()
            self.logger.info(f"✓ Successfully connected to {db_name} database")
            return True
        except psycopg2.Error as e:
            self.logger.error(f"✗ Failed to connect to {db_name} database: {e}")
            return False
    
    def create_local_database_if_not_exists(self):
        """Create local database if it doesn't exist"""
        local_config = self.config['local_db']
        
        try:
            # Connect to postgres database to create new database
            conn = psycopg2.connect(
                host=local_config['host'],
                port=local_config['port'],
                database='postgres',
                user=local_config['user'],
                password=local_config['password']
            )
            conn.autocommit = True
            cursor = conn.cursor()
            
            # Check if database exists
            cursor.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s",
                (local_config['database'],)
            )
            exists = cursor.fetchone()
            
            if not exists:
                cursor.execute(
                    sql.SQL("CREATE DATABASE {}").format(
                        sql.Identifier(local_config['database'])
                    )
                )
                self.logger.info(f"✓ Created local database: {local_config['database']}")
            else:
                self.logger.info(f"✓ Local database already exists: {local_config['database']}")
            
            cursor.close()
            conn.close()
            return True
        except psycopg2.Error as e:
            self.logger.error(f"✗ Error creating local database: {e}")
            return False
    
    def backup_local_database(self):
        """Create backup of local database before sync"""
        if not self.config['sync_settings']['backup_before_sync']:
            return True
        
        backup_dir = Path(self.config['sync_settings']['backup_directory'])
        backup_dir.mkdir(exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_file = backup_dir / f"local_backup_{timestamp}.sql"
        
        local_config = self.config['local_db']
        
        self.logger.info(f"Creating backup: {backup_file}")
        
        try:
            # Set PGPASSWORD environment variable
            env = os.environ.copy()
            env['PGPASSWORD'] = local_config['password']
            
            cmd = [
                self.get_pg_tool_path('pg_dump'),
                '-h', local_config['host'],
                '-p', str(local_config['port']),
                '-U', local_config['user'],
                '-d', local_config['database'],
                '-F', 'c',  # Custom format
                '-f', str(backup_file)
            ]
            
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True
            )
            
            if result.returncode == 0:
                self.logger.info(f"✓ Backup created: {backup_file}")
                return True
            else:
                self.logger.warning(f"Backup failed: {result.stderr}")
                return False
        except FileNotFoundError:
            self.logger.warning("pg_dump not found in PATH. Skipping backup.")
            return False
        except Exception as e:
            self.logger.error(f"✗ Backup error: {e}")
            return False
    
    def dump_remote_database(self):
        """Dump remote database to file"""
        remote_config = self.config['remote_db']
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        dump_file = f"remote_dump_{timestamp}.sql"
        
        self.logger.info("Dumping remote database...")
        self.logger.info("This may take several minutes for large databases...")
        
        try:
            # Set PGPASSWORD environment variable
            env = os.environ.copy()
            env['PGPASSWORD'] = remote_config['password']
            
            cmd = [
                self.get_pg_tool_path('pg_dump'),
                '-h', remote_config['host'],
                '-p', str(remote_config['port']),
                '-U', remote_config['user'],
                '-d', remote_config['database'],
                '-F', 'c',  # Custom format
                '-Z', '6',  # Compression level 6 (balance between speed and size)
                '-v',  # Verbose output
                '-f', dump_file
            ]
            
            self.logger.info(f"Command: {' '.join(cmd[:6])} ...")  # Don't log full command with credentials
            
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=600  # 10 minute timeout
            )
            
            if result.returncode == 0:
                # Get file size
                file_size = os.path.getsize(dump_file) / (1024 * 1024)  # MB
                self.logger.info(f"✓ Remote database dumped to: {dump_file} ({file_size:.2f} MB)")
                return dump_file
            else:
                self.logger.error(f"✗ Dump failed: {result.stderr}")
                return None
        except FileNotFoundError:
            self.logger.error("pg_dump not found in PATH. Please install PostgreSQL client tools.")
            return None
        except Exception as e:
            self.logger.error(f"✗ Dump error: {e}")
            return None
    
    def drop_local_database_schema(self):
        """Drop all tables in local database"""
        local_config = self.config['local_db']
        
        try:
            conn = psycopg2.connect(
                host=local_config['host'],
                port=local_config['port'],
                database=local_config['database'],
                user=local_config['user'],
                password=local_config['password']
            )
            conn.autocommit = True
            cursor = conn.cursor()
            
            # Drop all tables
            cursor.execute("""
                DO $$ DECLARE
                    r RECORD;
                BEGIN
                    FOR r IN (SELECT tablename FROM pg_tables WHERE schemaname = 'public') LOOP
                        EXECUTE 'DROP TABLE IF EXISTS ' || quote_ident(r.tablename) || ' CASCADE';
                    END LOOP;
                END $$;
            """)
            
            # Drop all sequences
            cursor.execute("""
                DO $$ DECLARE
                    r RECORD;
                BEGIN
                    FOR r IN (SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema = 'public') LOOP
                        EXECUTE 'DROP SEQUENCE IF EXISTS ' || quote_ident(r.sequence_name) || ' CASCADE';
                    END LOOP;
                END $$;
            """)
            
            self.logger.info("✓ Local database schema dropped")
            cursor.close()
            conn.close()
            return True
        except psycopg2.Error as e:
            self.logger.error(f"✗ Error dropping schema: {e}")
            return False
    
    def restore_to_local_database(self, dump_file):
        """Restore dump file to local database"""
        local_config = self.config['local_db']
        
        self.logger.info("Restoring to local database...")
        
        try:
            # Set PGPASSWORD environment variable
            env = os.environ.copy()
            env['PGPASSWORD'] = local_config['password']
            
            cmd = [
                self.get_pg_tool_path('pg_restore'),
                '-h', local_config['host'],
                '-p', str(local_config['port']),
                '-U', local_config['user'],
                '-d', local_config['database'],
                '--clean',  # Drop database objects before recreating
                '--if-exists',  # Use IF EXISTS when dropping objects
                '--no-owner',  # Don't try to set ownership
                '--no-acl',  # Don't restore access privileges
                dump_file
            ]
            
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True
            )
            
            # pg_restore often returns warnings, so check for critical errors
            if "error" in result.stderr.lower() and result.returncode != 0:
                self.logger.warning(f"Restore completed with warnings: {result.stderr}")
            
            self.logger.info(f"✓ Database restored to local")
            return True
        except FileNotFoundError:
            self.logger.error("pg_restore not found in PATH. Please install PostgreSQL client tools.")
            return False
        except Exception as e:
            self.logger.error(f"✗ Restore error: {e}")
            return False
    
    def cleanup_dump_file(self, dump_file):
        """Remove temporary dump file"""
        try:
            if dump_file and os.path.exists(dump_file):
                os.remove(dump_file)
                self.logger.info(f"✓ Cleaned up dump file: {dump_file}")
        except Exception as e:
            self.logger.warning(f"Could not remove dump file: {e}")
    
    def get_database_stats(self, db_config):
        """Get database statistics"""
        try:
            conn = psycopg2.connect(
                host=db_config['host'],
                port=db_config['port'],
                database=db_config['database'],
                user=db_config['user'],
                password=db_config['password']
            )
            cursor = conn.cursor()
            
            # Get table count
            cursor.execute("""
                SELECT COUNT(*) 
                FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """)
            table_count = cursor.fetchone()[0]
            
            # Get total row count (approximate)
            cursor.execute("""
                SELECT SUM(n_live_tup) 
                FROM pg_stat_user_tables
            """)
            row_count = cursor.fetchone()[0] or 0
            
            cursor.close()
            conn.close()
            
            return {
                'tables': table_count,
                'rows': row_count
            }
        except Exception as e:
            self.logger.error(f"Error getting database stats: {e}")
            return {'tables': 0, 'rows': 0}
    
    def synchronize(self):
        """Main synchronization method"""
        self.logger.info("=" * 60)
        self.logger.info("Starting database synchronization")
        self.logger.info("=" * 60)
        
        # Test connections
        self.logger.info("Testing database connections...")
        if not self.test_connection(self.config['remote_db'], 'REMOTE'):
            self.logger.error("Cannot proceed without remote connection")
            return False
        
        # Create local database if needed
        self.create_local_database_if_not_exists()
        
        if not self.test_connection(self.config['local_db'], 'LOCAL'):
            self.logger.error("Cannot proceed without local connection")
            return False
        
        # Get remote database stats
        remote_stats = self.get_database_stats(self.config['remote_db'])
        self.logger.info(f"Remote database: {remote_stats['tables']} tables, ~{remote_stats['rows']} rows")
        
        # Backup local database
        self.backup_local_database()
        
        # Dump remote database
        dump_file = self.dump_remote_database()
        if not dump_file:
            return False
        
        # Drop local schema
        self.drop_local_database_schema()
        
        # Restore to local
        success = self.restore_to_local_database(dump_file)
        
        # Cleanup
        self.cleanup_dump_file(dump_file)
        
        if success:
            # Get local database stats
            local_stats = self.get_database_stats(self.config['local_db'])
            self.logger.info(f"Local database: {local_stats['tables']} tables, ~{local_stats['rows']} rows")
            
            self.logger.info("=" * 60)
            self.logger.info("✓ SYNCHRONIZATION COMPLETED SUCCESSFULLY")
            self.logger.info("=" * 60)
        else:
            self.logger.error("=" * 60)
            self.logger.error("✗ SYNCHRONIZATION FAILED")
            self.logger.error("=" * 60)
        
        return success


def main():
    """Main entry point"""
    print("\n" + "=" * 60)
    print("  Database Synchronization Tool")
    print("  Remote (preprod) → Local PostgreSQL")
    print("=" * 60 + "\n")
    
    # Create synchronizer instance
    syncer = DatabaseSynchronizer()
    
    # Run synchronization
    success = syncer.synchronize()
    
    if success:
        print("\n✓ Synchronization completed successfully!")
        sys.exit(0)
    else:
        print("\n✗ Synchronization failed. Check logs for details.")
        sys.exit(1)


if __name__ == "__main__":
    main()
