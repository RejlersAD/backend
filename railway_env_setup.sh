#!/bin/bash

# Railway Environment Variables Setup Script
# Copy these variables to Railway Dashboard > Variables tab

echo "======================================================================"
echo "Railway Environment Variables Configuration"
echo "======================================================================"
echo ""
echo "Go to: https://railway.app/dashboard"
echo "Select: AIFlow Backend Project"
echo "Click: Variables Tab"
echo ""
echo "Add the following variables:"
echo "======================================================================"
echo ""

echo "1. OPENAI_API_KEY"
echo "   Value: YOUR-ACTUAL-OPENAI-API-KEY-HERE"
echo "   (Get from https://platform.openai.com/api-keys)"
echo ""

echo "2. DEBUG (if not already set)"
echo "   Value: False"
echo ""

echo "3. ALLOWED_HOSTS (if not already set)"
echo "   Value: .railway.app,.vercel.app"
echo ""

echo "4. CORS_ALLOWED_ORIGINS (if not already set)"
echo "   Value: https://airflow-frontend.vercel.app"
echo ""

echo "======================================================================"
echo "After adding variables:"
echo "  1. Click 'Deploy' or wait for auto-deployment"
echo "  2. Wait 1-2 minutes for deployment to complete"
echo "  3. Test at: https://airflow-frontend.vercel.app/"
echo "======================================================================"
