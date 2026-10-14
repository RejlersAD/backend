"""Exercise actual 0011/0013 DDL in a disposable local PostgreSQL database.

Requires explicit PGPORT/PGPASSWORD. Never reads application DB configuration.
"""
import importlib
import os
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from django.conf import settings

port = os.environ.get('PGPORT', '')
if not port.isdecimal() or int(port) == 5432 or not os.environ.get('PGPASSWORD'):
    raise RuntimeError('Explicit disposable PostgreSQL port and password required.')
settings.configure(
    SECRET_KEY='synthetic-only', INSTALLED_APPS=[], AUTH_USER_MODEL='users.User',
    DATABASES={'default': {
        'ENGINE': 'django.db.backends.postgresql', 'HOST': '127.0.0.1',
        'PORT': port, 'NAME': 'spec_key_repair_test', 'USER': 'spec_key_test',
        'PASSWORD': os.environ['PGPASSWORD'],
    }},
)
import django
django.setup()
from django.db import connection, models, transaction, IntegrityError
from django.db.migrations.state import ProjectState, ModelState

if connection.introspection.table_names():
    raise RuntimeError('Refusing a populated test database.')
baseline = ProjectState()
baseline.add_model(ModelState('users', 'User', [('id', models.BigAutoField(primary_key=True))]))
baseline.add_model(ModelState('spec_customization', 'SpecProject', [
    ('project_id', models.UUIDField(primary_key=True)),
]))
m11 = importlib.import_module('apps.spec_customization.migrations.0011_projectcreationrequest')
m13 = importlib.import_module('apps.spec_customization.migrations.0013_verify_project_reference_keys')

variants = {
    'missing key': '',
    'healthy primary key': ', PRIMARY KEY (project_id)',
    'existing other primary key': ', legacy_id bigserial PRIMARY KEY',
    'partial unique index': '',
    'deferrable unique': ', UNIQUE (project_id) DEFERRABLE',
    'missing user key': '',
    'duplicate IDs': '',
    'null IDs': '',
}
for label, extra in variants.items():
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE users_user (id bigint' + (')' if label == 'missing user key' else ' PRIMARY KEY)'))
            cursor.execute(f'CREATE TABLE spec_customization_specproject (project_id uuid{extra})')
            if label == 'partial unique index':
                cursor.execute('CREATE UNIQUE INDEX partial_project_key ON spec_customization_specproject (project_id) WHERE project_id IS NOT NULL')
            project = uuid4()
            cursor.execute('INSERT INTO spec_customization_specproject (project_id) VALUES (%s)', [project])
            if label == 'duplicate IDs':
                cursor.execute('INSERT INTO spec_customization_specproject (project_id) VALUES (%s)', [project])
            if label == 'null IDs':
                cursor.execute('INSERT INTO spec_customization_specproject (project_id) VALUES (NULL)')
            cursor.execute('SELECT * FROM spec_customization_specproject')
            original = cursor.fetchall()
        if label in ('duplicate IDs', 'null IDs'):
            try:
                with connection.schema_editor() as editor:
                    m11.Migration('0011_projectcreationrequest', 'spec_customization').apply(baseline.clone(), editor)
            except RuntimeError as error:
                assert ('duplicate IDs' if label == 'duplicate IDs' else 'null IDs') in str(error)
            else:
                raise AssertionError(f'Accepted {label}')
        else:
            with connection.schema_editor() as editor:
                state = m11.Migration('0011_projectcreationrequest', 'spec_customization').apply(baseline.clone(), editor)
            for _ in range(2):
                with connection.schema_editor() as editor:
                    m13.Migration('0013_verify_project_reference_keys', 'spec_customization').apply(state.clone(), editor)
            with connection.cursor() as cursor:
                cursor.execute('INSERT INTO users_user VALUES (1)')
                cursor.execute('INSERT INTO spec_customization_projectcreationrequest (id, request_id, payload_hash, created_at, owner_id, project_id) VALUES (%s, %s, %s, CURRENT_TIMESTAMP, 1, %s)', [uuid4(), uuid4(), 'synthetic', project])
                cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
            try:
                with transaction.atomic(), connection.cursor() as cursor:
                    cursor.execute('UPDATE spec_customization_projectcreationrequest SET project_id = %s', [uuid4()])
            except IntegrityError:
                pass
            else:
                raise AssertionError('Missing project foreign key enforcement')
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM spec_customization_specproject')
            assert cursor.fetchall() == original
        transaction.set_rollback(True)
    print(f'PASS: {label}; original rows preserved', flush=True)
print('PASS: actual 0011/0013 operations, repeated repair and PostgreSQL FK enforcement')
