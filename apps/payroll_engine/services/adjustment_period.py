from django.utils import timezone
from rest_framework.exceptions import ValidationError


def require_current_or_future(year, month):
    try:
        year, month = int(year), int(month)
    except (TypeError, ValueError):
        raise ValidationError('Select a valid adjustment month.')
    if not 1 <= month <= 12:
        raise ValidationError('Select a valid adjustment month.')
    today = timezone.localdate()
    if (year, month) < (today.year, today.month):
        raise ValidationError('Adjustments can only apply to the current month or a future month.')
