"""Validate explicit calendar shifts without supplying assumed working hours."""
from decimal import Decimal, InvalidOperation
import re


def interval_error(intervals, hours=None):
    if hours is not None:
        try:
            number = Decimal(str(hours))
            if not number.is_finite() or not 0 <= number <= 24:
                raise ValueError
        except (InvalidOperation, ValueError, TypeError):
            return 'Working hours must be a finite number from 0 to 24.'
    if not isinstance(intervals, list):
        return 'Working times must be an ordered list of from/to intervals.'
    previous = -1
    seconds = 0
    for interval in intervals:
        if not isinstance(interval, dict) or set(interval) != {'from', 'to'}:
            return 'Every working interval requires only from and to times.'
        values = []
        for key in ('from', 'to'):
            value = interval[key]
            if not isinstance(value, str) or not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d', value):
                return 'Use local working times in HH:MM:SS format.'
            hour, minute, second = map(int, value.split(':'))
            values.append(hour * 3600 + minute * 60 + second)
        start, end = values
        if start < previous or end <= start:
            return 'Working intervals must be ordered, nonoverlapping and within one day.'
        seconds += end - start
        previous = end
    if intervals and hours is not None and Decimal(seconds) != Decimal(str(hours)) * 3600:
        return 'Working intervals must total the explicitly configured working hours.'
    return None


def calendar_intervals_error(calendar):
    shifts = calendar.get('working_times', {})
    if not isinstance(shifts, dict):
        return 'Calendar working times must map weekday numbers to working intervals.'
    days = {str(day) for day in calendar.get('working_weekdays', [])}
    if set(shifts) - days:
        return 'Working times may only be supplied for configured working weekdays.'
    for intervals in shifts.values():
        error = interval_error(intervals, calendar.get('hours_per_day'))
        if error:
            return error
    for exception in calendar.get('exceptions', []):
        intervals = exception.get('working_times', [])
        hours = exception.get('working_hours')
        error = interval_error(intervals, hours)
        if error:
            return error
        if not exception.get('is_working') and hours is not None and Decimal(str(hours)) != 0:
            return 'Nonworking exceptions can only specify zero working hours.'
        if intervals and not exception.get('is_working'):
            return 'Nonworking exceptions cannot contain working intervals.'
        error = interval_error(intervals, hours if hours is not None else calendar.get('hours_per_day'))
        if error:
            return error
    return None
