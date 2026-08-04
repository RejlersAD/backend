"""
Real-time Database Synchronization Scheduler
Continuously monitors and syncs database at specified intervals
"""

import time
import json
import sys
import signal
from datetime import datetime, timedelta
from db_sync import DatabaseSynchronizer


class RealtimeDatabaseSync:
    def __init__(self, config_file='db_sync_config.json'):
        """Initialize real-time sync"""
        self.config_file = config_file
        self.load_config()
        self.syncer = DatabaseSynchronizer(config_file)
        self.running = True
        self.sync_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.last_sync_time = None
        
        # Setup signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)
    
    def load_config(self):
        """Load configuration"""
        try:
            with open(self.config_file, 'r') as f:
                config = json.load(f)
            self.sync_interval = config['sync_settings']['sync_interval_seconds']
            print(f"✓ Loaded configuration: Sync interval = {self.sync_interval} seconds")
        except Exception as e:
            print(f"✗ Error loading configuration: {e}")
            sys.exit(1)
    
    def signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        print("\n\nReceived shutdown signal. Stopping synchronization...")
        self.running = False
    
    def print_status(self):
        """Print current status"""
        print("\n" + "=" * 60)
        print(f"  Real-time Sync Status - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60)
        print(f"  Total syncs:        {self.sync_count}")
        print(f"  Successful:         {self.success_count}")
        print(f"  Failed:             {self.fail_count}")
        if self.last_sync_time:
            print(f"  Last sync:          {self.last_sync_time.strftime('%Y-%m-%d %H:%M:%S')}")
            next_sync = self.last_sync_time + timedelta(seconds=self.sync_interval)
            print(f"  Next sync:          {next_sync.strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 60 + "\n")
    
    def countdown_display(self, seconds):
        """Display countdown until next sync"""
        try:
            for remaining in range(seconds, 0, -1):
                if not self.running:
                    break
                
                mins, secs = divmod(remaining, 60)
                hours, mins = divmod(mins, 60)
                
                time_str = f"{hours:02d}:{mins:02d}:{secs:02d}"
                print(f"\rNext sync in: {time_str} | Press Ctrl+C to stop", end='', flush=True)
                time.sleep(1)
            print()  # New line after countdown
        except KeyboardInterrupt:
            self.running = False
    
    def run_continuous(self):
        """Run continuous synchronization"""
        print("\n" + "=" * 60)
        print("  REAL-TIME DATABASE SYNCHRONIZATION")
        print("  Remote (preprod) → Local PostgreSQL")
        print("=" * 60)
        print(f"\n  Sync interval: {self.sync_interval} seconds ({self.sync_interval/60:.1f} minutes)")
        print("  Press Ctrl+C to stop")
        print("=" * 60 + "\n")
        
        # Initial sync on startup
        print("Running initial synchronization...\n")
        success = self.syncer.synchronize()
        self.sync_count += 1
        self.last_sync_time = datetime.now()
        
        if success:
            self.success_count += 1
            print("\n✓ Initial sync completed successfully")
        else:
            self.fail_count += 1
            print("\n✗ Initial sync failed")
        
        # Continuous sync loop
        while self.running:
            try:
                self.print_status()
                
                # Wait for next sync interval
                print(f"Waiting {self.sync_interval} seconds until next sync...")
                self.countdown_display(self.sync_interval)
                
                if not self.running:
                    break
                
                # Run synchronization
                print("\nRunning scheduled synchronization...\n")
                success = self.syncer.synchronize()
                self.sync_count += 1
                self.last_sync_time = datetime.now()
                
                if success:
                    self.success_count += 1
                    print("\n✓ Sync completed successfully")
                else:
                    self.fail_count += 1
                    print("\n✗ Sync failed")
                
            except KeyboardInterrupt:
                self.running = False
                break
            except Exception as e:
                print(f"\n✗ Error during sync: {e}")
                self.fail_count += 1
                self.sync_count += 1
        
        # Final status
        print("\n" + "=" * 60)
        print("  SYNCHRONIZATION STOPPED")
        print("=" * 60)
        print(f"  Total syncs:        {self.sync_count}")
        print(f"  Successful:         {self.success_count}")
        print(f"  Failed:             {self.fail_count}")
        print("=" * 60 + "\n")
    
    def run_once(self):
        """Run synchronization once"""
        print("\nRunning one-time synchronization...\n")
        success = self.syncer.synchronize()
        return success


def main():
    """Main entry point"""
    import argparse
    
    parser = argparse.ArgumentParser(
        description='Real-time Database Synchronization'
    )
    parser.add_argument(
        '--once',
        action='store_true',
        help='Run synchronization once and exit'
    )
    parser.add_argument(
        '--config',
        default='db_sync_config.json',
        help='Path to configuration file (default: db_sync_config.json)'
    )
    
    args = parser.parse_args()
    
    # Create real-time sync instance
    rt_sync = RealtimeDatabaseSync(args.config)
    
    if args.once:
        # Run once
        success = rt_sync.run_once()
        sys.exit(0 if success else 1)
    else:
        # Run continuously
        rt_sync.run_continuous()
        sys.exit(0)


if __name__ == "__main__":
    main()
