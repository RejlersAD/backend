"""
Centralized Environment Configuration Loader
============================================
This module provides soft-coded configuration management for AIFlow.
It reads from the centralized environments.json file to ensure alignment
between frontend, backend, and database configurations.

Based on commit: c6c3a7e (9-3-26 : First Commit)
Date: 2026-03-11
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional


class EnvironmentConfig:
    """Centralized configuration manager for AIFlow"""
    
    _instance = None
    _config = None
    
    def __new__(cls):
        """Singleton pattern to ensure single config instance"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        """Initialize configuration"""
        if self._config is None:
            self._load_config()
    
    def _load_config(self):
        """Load configuration from environments.json"""
        # Find the config file — check multiple locations for different deployment contexts:
        # 1. Monorepo root (local Docker): /config/environments.json
        # 2. Same directory as this file (Railway nixpacks, backend submodule deployed alone)
        current_dir = Path(__file__).resolve().parent.parent  # backend directory
        candidates = [
            current_dir.parent / 'config' / 'environments.json',  # monorepo root
            Path(__file__).resolve().parent / 'environments.json',  # local copy in backend/config/
        ]
        config_file = next((p for p in candidates if p.exists()), None)

        if not config_file:
            print(f"[CONFIG] WARNING: environments.json not found in any of: {[str(p) for p in candidates]}")
            self._config = {}
            return
        
        try:
            with open(config_file, 'r') as f:
                self._config = json.load(f)
            print(f"[CONFIG] ✅ Loaded configuration from {config_file}")
        except Exception as e:
            print(f"[CONFIG] ERROR: Failed to load configuration: {e}")
            self._config = {}
    
    def get_environment(self) -> str:
        """
        Detect current environment (local, dev, preprod, production)
        
        Returns:
            str: Environment name
        """
        # Check for explicit environment variable
        env = os.environ.get('AIFLOW_ENVIRONMENT', '').lower()
        if env in ['local', 'dev', 'preprod', 'production']:
            return env
        
        # Detect based on Railway environment
        railway_env = os.environ.get('RAILWAY_ENVIRONMENT', '').lower()
        if railway_env:
            if railway_env == 'production':
                return 'production'
            elif railway_env == 'preprod':
                return 'preprod'
            elif railway_env == 'dev':
                return 'dev'
        
        # Check for DATABASE_URL (Railway/production indicator)
        if os.environ.get('DATABASE_URL'):
            # If DATABASE_URL exists, we're in Railway - check branch
            git_branch = os.environ.get('RAILWAY_GIT_BRANCH', '')
            if 'main' in git_branch or 'production' in git_branch:
                return 'production'
            elif 'preprod' in git_branch:
                return 'preprod'
            elif 'dev' in git_branch:
                return 'dev'
            # Default Railway to dev
            return 'dev'
        
        # Default to local
        return 'local'
    
    def get(self, key: str, environment: Optional[str] = None, default: Any = None) -> Any:
        """
        Get configuration value for current or specified environment
        
        Args:
            key: Configuration key (e.g., 'backend.url')
            environment: Environment name (defaults to current)
            default: Default value if key not found
        
        Returns:
            Configuration value
        """
        if not environment:
            environment = self.get_environment()
        
        # Navigate nested dictionary
        keys = key.split('.')
        value = self._config
        
        # Check if we need environment-specific config
        if keys[0] == 'environments':
            keys.pop(0)  # Remove 'environments' prefix
        
        # Add environment to path if not already there
        if keys and keys[0] not in ['environments']:
            keys = ['environments', environment] + keys
        else:
            keys = ['environments', environment] + keys[1:]
        
        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default
    
    def get_backend_config(self, environment: Optional[str] = None) -> Dict[str, Any]:
        """Get backend configuration for environment"""
        if not environment:
            environment = self.get_environment()
        return self._config.get('environments', {}).get(environment, {}).get('backend', {})
    
    def get_frontend_config(self, environment: Optional[str] = None) -> Dict[str, Any]:
        """Get frontend configuration for environment"""
        if not environment:
            environment = self.get_environment()
        return self._config.get('environments', {}).get(environment, {}).get('frontend', {})
    
    def get_database_config(self, environment: Optional[str] = None) -> Dict[str, Any]:
        """Get database configuration for environment"""
        if not environment:
            environment = self.get_environment()
        return self._config.get('environments', {}).get(environment, {}).get('database', {})
    
    def get_cors_origins(self, environment: Optional[str] = None) -> list:
        """Get CORS allowed origins for environment"""
        if not environment:
            environment = self.get_environment()
        return self._config.get('features', {}).get('cors', {}).get('allowed_origins', {}).get(environment, [])
    
    def get_csrf_origins(self, environment: Optional[str] = None) -> list:
        """Get CSRF trusted origins for environment"""
        if not environment:
            environment = self.get_environment()
        return self._config.get('features', {}).get('csrf', {}).get('trusted_origins', {}).get(environment, [])
    
    def get_api_config(self) -> Dict[str, Any]:
        """Get API configuration"""
        return self._config.get('api', {})
    
    def print_current_config(self):
        """Print current environment configuration (debug helper)"""
        env = self.get_environment()
        print(f"\n{'='*60}")
        print(f"[CONFIG] Current Environment: {env}")
        print(f"{'='*60}")
        
        backend = self.get_backend_config()
        print(f"\n[BACKEND]")
        for key, value in backend.items():
            print(f"  {key}: {value}")
        
        frontend = self.get_frontend_config()
        print(f"\n[FRONTEND]")
        for key, value in frontend.items():
            print(f"  {key}: {value}")
        
        database = self.get_database_config()
        print(f"\n[DATABASE]")
        for key, value in database.items():
            # Don't print passwords
            if 'password' in key.lower():
                print(f"  {key}: ****")
            else:
                print(f"  {key}: {value}")
        
        print(f"\n[CORS] Allowed Origins: {self.get_cors_origins()}")
        print(f"[CSRF] Trusted Origins: {self.get_csrf_origins()}")
        print(f"{'='*60}\n")


# Singleton instance
config = EnvironmentConfig()


# Convenience functions
def get_environment() -> str:
    """Get current environment name"""
    return config.get_environment()


def get_backend_url(environment: Optional[str] = None) -> str:
    """Get backend URL for environment"""
    backend_config = config.get_backend_config(environment)
    return backend_config.get('url', 'http://localhost:8000')


def get_api_url(environment: Optional[str] = None) -> str:
    """Get API URL for environment"""
    backend_config = config.get_backend_config(environment)
    return backend_config.get('api_url', 'http://localhost:8000/api/v1')


def get_database_url(environment: Optional[str] = None) -> Optional[str]:
    """
    Get database URL if available
    
    For Railway/cloud environments, returns the DATABASE_URL env var.
    For local environments, constructs URL from individual components.
    """
    db_config = config.get_database_config(environment)
    
    # Check if should use URL from environment variable
    if db_config.get('use_url'):
        url_env_var = db_config.get('url_env_var', 'DATABASE_URL')
        return os.environ.get(url_env_var)
    
    # Construct URL from components
    engine = db_config.get('engine', 'postgresql')
    user = db_config.get('user', 'postgres')
    password = db_config.get('password', 'postgres123')
    host = db_config.get('host', 'localhost')
    port = db_config.get('port', 5432)
    name = db_config.get('name', 'radai_db')
    
    return f"{engine}://{user}:{password}@{host}:{port}/{name}"


def get_cors_origins(environment: Optional[str] = None) -> list:
    """Get CORS allowed origins for environment"""
    return config.get_cors_origins(environment)


def get_csrf_origins(environment: Optional[str] = None) -> list:
    """Get CSRF trusted origins for environment"""
    return config.get_csrf_origins(environment)


# Print configuration on import (only if DEBUG or in local environment)
if os.environ.get('DEBUG', 'False').lower() == 'true' or config.get_environment() == 'local':
    config.print_current_config()


if __name__ == '__main__':
    # Test the configuration
    config.print_current_config()
    
    print("\n[TEST] Testing configuration functions:")
    print(f"  Environment: {get_environment()}")
    print(f"  Backend URL: {get_backend_url()}")
    print(f"  API URL: {get_api_url()}")
    print(f"  Database URL: {get_database_url()}")
    print(f"  CORS Origins: {get_cors_origins()}")
    print(f"  CSRF Origins: {get_csrf_origins()}")
