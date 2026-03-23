import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.pid_analysis.services import PIDAnalysisService

svc = PIDAnalysisService.__new__(PIDAnalysisService)
svc.instrument_tags = {'FE-4580', 'FI-4580', 'FT-4580', 'HIC-4587', 'HV-4587', 'HY-4587', 'PT-4587', 'TSHH-4580', 'XV-4513', 'XV-7201'}
svc.line_numbers = ['13-FE-4580', '13-FI-4580', '13-FT-4580', '13-HIC-4587', '13-HV-4587', '13-HY-4587', '13-KX-402', '13-PG-4586', '13-PT-4587', '13-SP-4300', '13-SP-4301', '13-TA-4580', '13-TDA-4580', '13-TSHH-4580', '13-VV-705', '13-XV-4513', '13-XV-7201', '13-XY-4513', '13-XZLH-4513', '13-XZLL-4513', '13-XZSH-4513', '13-XZSL-4513', '37-PU-152-09918', '50-HE-1402', '50-VV-1401']

result = svc._build_per_instrument_instructions()
print('=== CHECKLIST OUTPUT ===')
print(result)
print(f'\n=== TOTAL LENGTH: {len(result)} chars ===')
