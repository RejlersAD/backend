#!/usr/bin/env python
"""Test DNS and Network connectivity"""
import socket
import sys

print('=' * 70)
print('NETWORK & DNS TEST')
print('=' * 70)

# Test DNS resolution
hosts_to_test = [
    'email-smtp.me-central-1.amazonaws.com',
    'google.com',
    'amazonaws.com'
]

print('\n--- DNS Resolution Test ---')
for host in hosts_to_test:
    try:
        ip = socket.gethostbyname(host)
        print(f'✓ {host} -> {ip}')
    except socket.gaierror as e:
        print(f'✗ {host} -> DNS Error: {e}')
    except Exception as e:
        print(f'✗ {host} -> Error: {e}')

# Test SMTP port connectivity
print('\n--- SMTP Port Test ---')
smtp_host = 'email-smtp.me-central-1.amazonaws.com'
smtp_port = 587

try:
    print(f'Testing {smtp_host}:{smtp_port}...')
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    result = sock.connect_ex((smtp_host, smtp_port))
    sock.close()
    
    if result == 0:
        print(f'✓ Port {smtp_port} is open and accessible')
    else:
        print(f'✗ Port {smtp_port} is not accessible (error code: {result})')
except socket.gaierror as e:
    print(f'✗ DNS resolution failed: {e}')
except Exception as e:
    print(f'✗ Connection test failed: {e}')

print('\n' + '=' * 70)
