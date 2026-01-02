#!/bin/bash

# ==========================================
# Railway Deployment Pre-Build Script
# Cleans cache and optimizes for faster deployment
# ==========================================

echo "🧹 Cleaning Python cache files..."

# Remove all __pycache__ directories
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

# Remove all .pyc files
find . -type f -name "*.pyc" -delete 2>/dev/null || true

# Remove all .pyo files
find . -type f -name "*.pyo" -delete 2>/dev/null || true

echo "✅ Cache cleaned successfully!"
echo "📦 Ready for Railway deployment"
