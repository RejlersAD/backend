#!/bin/bash
# Auto-fix script for Railway deployment
# Add this to Railway's deploy command: bash auto_fix_procurement.sh

echo "========================================"
echo "Auto-fixing Procurement Module"
echo "========================================"

# Run the fix command
python manage.py fix_production_procurement --seed

# Check final status
python manage.py check_procurement_status

echo "========================================"
echo "Auto-fix Complete"
echo "========================================"
