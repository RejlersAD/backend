"""Create a conservative, non-release PMS SPEC review copy.

Only literal PDF class headings establish valid SPECNAME values. Unsupported
engineering rules and generated identifiers are held for department review.
The source workbook is never modified.
"""
import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pymupdf
from openpyxl import load_workbook

from apps.spec_customization.services.pms_source_guard import CLASS_HEADING


HELD_RULE_SHEETS = (
    'PipeBranch', 'PermissibleTaps', 'ThicknessDataRule',
    'NutSelectionFilter', 'PipingCommodityMatlControlData',
)
PCF_BLANK = (
    'COMMENTS', 'MAXIMUMTEMPERATURE', 'MINIMUMTEMPERATURE', 'ENGINEERINGTAG',
    'REPORTABLECOMMODITYCODE', 'ASSOCIATEDCOMMODITYCODE',
    'ALTREPORTABLECOMMODITYCODE',
)
SCHEDULE = re.compile(r'(?i)^(?:SCH(?:EDULE)?\.?\s*|S[-\s]?)?(10S|40S|80S|10|20|30|40|60|80|100|120|140|160|STD|XS|XXS)$')
VALID_SCHEDULES = {
    'S-10S', 'S-40S', 'S-80S', 'S-10', 'S-20', 'S-30', 'S-40',
    'S-60', 'S-80', 'S-100', 'S-120', 'S-140', 'S-160',
    'S-STD', 'S-XS', 'S-XXS',
}


def headings(pdf):
    found = defaultdict(list)
    with pymupdf.open(pdf) as document:
        for page, page_obj in enumerate(document, 1):
            for match in CLASS_HEADING.finditer(page_obj.get_text('text') or ''):
                found[match.group('code').upper()].append(page)
    return found


def columns(sheet):
    return {str(c.value).upper(): c.column for c in sheet[4] if c.value is not None}


def data_rows(sheet):
    return range(6, sheet.max_row + 1)


def remove_rows(sheet, row_numbers):
    ordered = sorted(set(row_numbers))
    if not ordered:
        return 0
    groups = []
    start = last = ordered[0]
    for row in ordered[1:]:
        if row == last + 1:
            last = row
        else:
            groups.append((start, last - start + 1))
            start = last = row
    groups.append((start, last - start + 1))
    for start, count in reversed(groups):
        sheet.delete_rows(start, count)
    return len(ordered)


def run(source, pdf, destination):
    if source.resolve() == destination.resolve():
        raise ValueError('Destination must differ from source workbook')
    valid = headings(pdf)
    if not valid:
        raise ValueError('No literal PMS class headings found in PDF')
    book = load_workbook(source)
    changes = Counter()
    held = defaultdict(Counter)
    for sheet in book:
        col = columns(sheet)
        spec_col = col.get('SPECNAME')
        if spec_col:
            bad = []
            for row in data_rows(sheet):
                raw = sheet.cell(row, spec_col).value
                if raw is not None and str(raw).strip().upper() not in valid:
                    bad.append(row)
                    held[str(raw).strip().upper()][sheet.title] += 1
            changes[f'{sheet.title}:invalid_class_rows_removed'] += remove_rows(sheet, bad)
        if sheet.title in HELD_RULE_SHEETS:
            n = max(0, sheet.max_row - 5)
            if n:
                if spec_col:
                    for row in data_rows(sheet):
                        spec = sheet.cell(row, spec_col).value
                        if spec is not None:
                            held[str(spec).strip().upper()][sheet.title] += 1
                sheet.delete_rows(6, n)
            changes[f'{sheet.title}:unverified_rule_rows_held'] += n
        if sheet.title == 'PipingCommodityFilter':
            for row in data_rows(sheet):
                for field in PCF_BLANK:
                    idx = col.get(field)
                    if idx and sheet.cell(row, idx).value is not None:
                        sheet.cell(row, idx).value = None
                        changes[f'PCF:{field}_cleared'] += 1
                code_cell = sheet.cell(row, col['COMMODITYCODE'])
                if str(code_cell.value or '').startswith('RAD_'):
                    code_cell.value = None
                    changes['PCF:synthetic_commodity_code_held'] += 1
                fluid = sheet.cell(row, col['FLUIDCODE'])
                if fluid.value != 1:
                    fluid.value = 1
                    changes['PCF:fluid_code_set_to_1'] += 1
                short = sheet.cell(row, col['SHORTCODE'])
                if str(short.value or '').casefold() == 'cap':
                    short.value = 'CAP'
                    changes['PCF:cap_normalized'] += 1
                for field in ('FIRSTSIZESCHEDULE', 'SECONDSIZESCHEDULE'):
                    idx = col.get(field)
                    if not idx:
                        continue
                    cell = sheet.cell(row, idx)
                    value = str(cell.value or '').strip()
                    if not value:
                        continue
                    match = SCHEDULE.fullmatch(value)
                    if match:
                        canonical = 'S-' + match.group(1).upper()
                        if canonical in VALID_SCHEDULES and value != canonical:
                            cell.value = canonical
                            changes[f'PCF:{field}_normalized'] += 1
                    elif value not in VALID_SCHEDULES:
                        held[str(sheet.cell(row, col['SPECNAME']).value)][field] += 1
    destination.parent.mkdir(parents=True, exist_ok=True)
    class_sheet = book['PipingMaterialsClassData']
    class_col = columns(class_sheet)['SPECNAME']
    workbook_classes = {
        str(class_sheet.cell(row, class_col).value).strip().upper()
        for row in data_rows(class_sheet) if class_sheet.cell(row, class_col).value
    }
    limit_sheet = book['ServiceLimits']
    limit_col = columns(limit_sheet)['SPECNAME']
    limit_classes = {
        str(limit_sheet.cell(row, limit_col).value).strip().upper()
        for row in data_rows(limit_sheet) if limit_sheet.cell(row, limit_col).value
    }
    book.save(destination)
    report = {
        'source_workbook': str(source), 'source_pdf': str(pdf),
        'output_workbook': str(destination), 'release_ready': False,
        'verified_class_headings': len(valid), 'changes': dict(changes),
        'workbook_classes': len(workbook_classes),
        'pdf_heading_classes_not_in_workbook': sorted(set(valid) - workbook_classes),
        'classes_without_service_limits': sorted(workbook_classes - limit_classes),
        'held_review_counts_by_spec': {k: dict(v) for k, v in held.items()},
        'notes': [
            'Generated RAD_ commodity codes were cleared; approved catalog mapping is required.',
            'Branch, tap, thickness and nut rules were held pending class-specific engineering approval.',
            'Other workbook defaults and catalog identifiers still require evidence review.',
        ],
    }
    report_path = destination.with_suffix('.review.json')
    report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report_path, changes


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('source', type=Path)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    print(run(args.source, args.pdf, args.destination))
