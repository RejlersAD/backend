"""
Smart Area Population Script
Parses existing line numbers to extract and populate area field
"""
import os
import django
import re

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.designiq.models import EngineeringListItem

def extract_area_from_line_number(line_number):
    """
    Extract area from line number using intelligent pattern matching
    
    Formats:
    1. General (Onshore with area): SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS-INSULATION
       Example: 1"-41-SWS-64544-A2AU16-V → area=41
       
    2. Offshore: AREA-FLUID-SIZE-PIPECLASS-SEQUENCE-INSULATION
       Example: 604-HO-8-BC2GA0-1071-H → area=604
       
    3. Onshore (no area): SIZE-FLUID-SEQUENCE-PIPECLASS-INSULATION
       Example: 12-D-5777-033842-N → area='' (empty)
    """
    line_number = line_number.strip()
    
    # Pattern 1: General format with area (SIZE"-AREA-FLUID...)
    # Match: 1"-41-SWS-... or 14"-41-SWS-...
    general_pattern = r'^(\d{1,2})["\']?\s*-\s*(\d{2,3})\s*-\s*([A-Z]{2,3})'
    match = re.match(general_pattern, line_number, re.IGNORECASE)
    if match:
        area = match.group(2)  # Extract the area code (2-3 digits)
        print(f"  ✓ General format: {line_number} → area={area}")
        return area
    
    # Pattern 2: Offshore format (AREA-FLUID-SIZE...)
    # Match: 604-HO-8-... or 41-SWS-12-...
    # Area is at START, followed by 2-3 letter fluid code
    offshore_pattern = r'^(\d{2,3})\s*-\s*([A-Z]{2,3})\s*-\s*(\d{1,2})["\']?\s*-'
    match = re.match(offshore_pattern, line_number, re.IGNORECASE)
    if match:
        area = match.group(1)  # Extract the area code at start
        print(f"  ✓ Offshore format: {line_number} → area={area}")
        return area
    
    # Pattern 3: Onshore without area (SIZE-FLUID-SEQUENCE...)
    # Match: 12-D-5777-... (no area field)
    onshore_pattern = r'^(\d{1,2})["\']?\s*-\s*([A-Z]{1,3})\s*-\s*(\d{4,6})'
    match = re.match(onshore_pattern, line_number, re.IGNORECASE)
    if match:
        print(f"  ✓ Onshore format (no area): {line_number} → area=''")
        return ''  # No area in onshore format
    
    print(f"  ✗ Unknown format: {line_number}")
    return ''

def main():
    print("\n" + "="*70)
    print("SMART AREA POPULATION - Extracting from Existing Line Numbers")
    print("="*70 + "\n")
    
    # Get all line list items
    items = EngineeringListItem.objects.filter(list_type='line_list').order_by('-created_at')
    total = items.count()
    
    print(f"Found {total} line items in database\n")
    
    updated_count = 0
    already_has_area = 0
    
    for idx, item in enumerate(items, 1):
        line_number = item.item_tag
        
        # Check if area already exists
        current_area = item.data.get('area', None)
        if current_area:
            already_has_area += 1
            continue
        
        # Extract area from line number
        area = extract_area_from_line_number(line_number)
        
        # Update the database
        item.data['area'] = area
        item.save()
        updated_count += 1
        
        if idx % 10 == 0:
            print(f"Progress: {idx}/{total} processed...")
    
    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)
    print(f"Total items: {total}")
    print(f"Already had area: {already_has_area}")
    print(f"Updated with area: {updated_count}")
    print(f"\n✅ Area field populated successfully!")
    print("="*70 + "\n")

if __name__ == '__main__':
    main()
