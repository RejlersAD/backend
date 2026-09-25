"""Carry explicit recommendation project numbers into the printed PO reference."""

import re

from .purchase_order_content import commercial_edit_locked


HISTORICAL_PROJECT_NUMBER = re.compile(r'(?:^|[^A-Za-z0-9])(\d{7})(?![A-Za-z0-9])')


def requisition_project_numbers(requisition):
    details = getattr(requisition, 'project_details', None)
    numbers, seen = [], set()

    def add(number):
        if number and number.casefold() not in seen:
            numbers.append(number)
            seen.add(number.casefold())

    for detail in details if isinstance(details, list) else []:
        if not isinstance(detail, dict):
            continue
        number = str(detail.get('project_number') or detail.get('project_code') or detail.get('code') or '').strip()
        if number:
            add(number)
        else:
            # Historical choices saved their seven-digit project number in a
            # display label. Smaller MOC/package numbers are not project IDs.
            label = str(detail.get('value') or detail.get('label') or '')
            for match in HISTORICAL_PROJECT_NUMBER.finditer(label):
                add(match.group(1))
    if not numbers:
        # Native Project Number and reviewed imports use an explicit CSV.
        # Preserve conservative parsing for historical free-text labels.
        project = str(getattr(requisition, 'project', '') or '').strip()
        references = [item.strip() for item in project.split(',') if item.strip()]
        if references and all(re.fullmatch(r'(?=.*\d)[A-Za-z0-9._/-]+', item) for item in references):
            for reference in references:
                add(reference)
    if not numbers:
        legacy_reference = str(getattr(requisition, 'project_department', '') or getattr(requisition, 'project', '') or '')
        for match in HISTORICAL_PROJECT_NUMBER.finditer(legacy_reference):
            add(match.group(1))
    return numbers


def requisition_project_reference(requisition):
    return ', '.join(requisition_project_numbers(requisition)) or str(getattr(requisition, 'project', '') or '').strip()


def purchase_order_project_reference(order):
    recorded = str(getattr(order, 'project_number', '') or '').strip()
    contacts = getattr(order, 'contact_persons', None)
    if isinstance(contacts, dict) and 'project_selections' in contacts:
        # Explicit add/remove choices supersede the legacy linked-PR fallback,
        # including deliberately removing every project from the document.
        return recorded
    recorded = recorded or str(getattr(order, 'rad_project_no', '') or '').strip()
    # A later edit to the linked PR cannot alter signed PO terms. New orders
    # save the full project reference before any approval can be recorded.
    if commercial_edit_locked(order):
        return recorded
    requisition = getattr(order, 'pr_reference', None)
    numbers = requisition_project_numbers(requisition)
    if numbers and (not recorded or recorded.casefold() in {number.casefold() for number in numbers}):
        return ', '.join(numbers)
    return recorded or requisition_project_reference(requisition) or str(getattr(requisition, 'project_department', '') or '').strip()
