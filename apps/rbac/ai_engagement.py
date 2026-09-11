"""Versioned engagement indicators; these are not productivity or award scores."""
from datetime import timedelta


VERSION = 'observed-engagement-40-30-30-v1'


def engagement_score(active_days, modules_used, entitled_modules, start, end):
    """Score a trailing 28-day window using distinct days and entitled modules.

    Frequency target: 20 active days per 28 calendar days. Consistency: use
    in each of four seven-day intervals. Fixed targets avoid peer-relative ranks.
    """
    dates = {d for d in active_days if start.date() <= d < end.date()}
    available = set(entitled_modules)
    used = set(modules_used) & available
    frequency = min(len(dates) / 20, 1) * 100
    breadth = len(used) / len(available) * 100 if available else 0
    weeks = sum(any((start + timedelta(days=7 * n)).date() <= d <
                    (start + timedelta(days=7 * (n + 1))).date() for d in dates)
                for n in range(4))
    consistency = weeks / 4 * 100
    score = round(.4 * frequency + .3 * breadth + .3 * consistency, 1)
    # No observations are insufficient evidence to assign a maturity level 0.
    band = ('Unknown' if not dates else 'Low' if score <= 30 else
            'Moderate' if score <= 60 else 'High' if score <= 80 else 'Power user')
    return {'score': score if dates else None, 'band': band,
            'frequency': round(frequency, 1), 'feature_usage': round(breadth, 1),
            'consistency': consistency, 'active_days': len(dates),
            'active_weeks': weeks, 'modules_used': len(used),
            'entitled_modules': len(available)}
