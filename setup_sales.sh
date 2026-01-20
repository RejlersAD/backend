#!/bin/bash
# Setup script for Sales RFP/EOI Automation Module

echo "========================================="
echo "Sales RFP/EOI Module Setup"
echo "========================================="

# Check if running in Docker
if [ -f "/.dockerenv" ]; then
    echo "✓ Running in Docker container"
    
    # Run migrations
    echo ""
    echo "Creating database migrations..."
    python manage.py makemigrations sales
    
    echo ""
    echo "Applying migrations..."
    python manage.py migrate sales
    
    echo ""
    echo "✓ Setup complete!"
    echo ""
    echo "Next steps:"
    echo "1. Configure environment variables in .env file"
    echo "2. Set up approval routes (already done via data migration)"
    echo "3. Test document upload via API"
    echo "4. Check README.md for API documentation"
    
else
    echo "⚠️  Not in Docker. Please run inside Docker container:"
    echo "docker exec -it aiflow_backend bash -c './setup_sales.sh'"
fi
