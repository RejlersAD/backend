"""Validate the minimum secure runtime configuration for Railway."""

import os
import sys


DEFAULT_SECRET_KEYS = {
    '',
    'django-insecure-change-this-in-production',
    'your-secret-key-change-this-in-production',
}


def first_value(*names):
    for name in names:
        value = os.environ.get(name, '').strip()
        if value:
            return value
    return ''


def main():
    environment = first_value(
        'ENVIRONMENT',
        'AIFLOW_ENVIRONMENT',
        'RAILWAY_ENVIRONMENT_NAME',
        'RAILWAY_ENVIRONMENT',
    ) or 'production'
    environment_key = environment.strip().lower()
    if environment_key in {'production', 'prod'}:
        database_url = first_value('DATABASE_URL', 'PRODUCTION_DATABASE_URL')
    elif environment_key in {'preprod', 'pre-production', 'staging'}:
        database_url = first_value('DATABASE_URL', 'TEST_DATABASE_URL')
    else:
        database_url = first_value(
            'DATABASE_URL', 'LOCAL_DATABASE_URL', 'TEST_DATABASE_URL'
        )
    secret_key = os.environ.get('SECRET_KEY', '').strip()
    secret_valid = secret_key not in DEFAULT_SECRET_KEYS and len(secret_key) >= 32

    errors = []
    if not database_url:
        errors.append(
            'DATABASE_URL is missing. Add a Railway reference variable such as '
            'DATABASE_URL=${{Postgres.DATABASE_URL}}.'
        )
    if not secret_valid:
        errors.append(
            'SECRET_KEY is missing or insecure. Set a stable random value of at '
            'least 32 characters in Railway.'
        )

    print(f'[PREFLIGHT] Environment: {environment}')
    print(f'[PREFLIGHT] Database URL: {"configured" if database_url else "missing"}')
    print(f'[PREFLIGHT] Secret key: {"configured" if secret_valid else "missing/insecure"}')

    if errors:
        print('[PREFLIGHT] Critical configuration errors:')
        for error in errors:
            print(f'  - {error}')
        return 1

    print('[PREFLIGHT] Runtime configuration is valid.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
