from apps.usage_tracking.models import UsageLog
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count

email = 'lira.viaga@rejlers.ae'
cutoff = timezone.now() - timedelta(days=30)

print('=== LIRA FEATURE USAGE ===')
results = UsageLog.objects.filter(
    user_email=email,
    timestamp__gte=cutoff
).values('discipline_key').annotate(count=Count('id')).order_by('-count')
for r in results:
    print(f"{r['discipline_key']}: {r['count']}")

print('=== LIRA TOTALS ===')
print('30d total:', UsageLog.objects.filter(user_email=email, timestamp__gte=cutoff).count())
print('Today:', UsageLog.objects.filter(user_email=email, timestamp__date=timezone.now().date()).count())
