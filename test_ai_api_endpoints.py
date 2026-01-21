"""
Test QHSE AI API Endpoints
Requires: Django dev server running (python manage.py runserver)
"""
import requests
import json

BASE_URL = "http://localhost:8000"
API_URL = f"{BASE_URL}/api/qhse/ai"

# You'll need to replace this with your actual auth token
AUTH_TOKEN = "your_token_here"  # Get from localStorage in browser

HEADERS = {
    "Authorization": f"Bearer {AUTH_TOKEN}",
    "Content-Type": "application/json"
}

print("=" * 70)
print("QHSE AI API Endpoint Tests")
print("=" * 70)
print(f"\n🌐 Base URL: {BASE_URL}")
print(f"🔗 API URL: {API_URL}\n")

# Test 1: AI Insights Dashboard
print("📊 Test 1: AI Insights Dashboard")
print(f"   GET {API_URL}/insights/")
try:
    response = requests.get(f"{API_URL}/insights/", headers=HEADERS)
    if response.status_code == 200:
        data = response.json()
        print(f"   ✅ Status: {response.status_code}")
        print(f"   📈 Projects Analyzed: {data.get('total_projects_analyzed', 0)}")
        print(f"   ⚠️  High Risk Projects: {data.get('high_risk_projects', 0)}")
        print(f"   📊 Average Risk Score: {data.get('average_risk_score', 0)}")
    else:
        print(f"   ❌ Status: {response.status_code}")
        print(f"   Error: {response.text}")
except Exception as e:
    print(f"   ❌ Connection failed: {str(e)}")
    print("   💡 Make sure Django server is running: python manage.py runserver")

# Test 2: Risk Prediction (all projects)
print("\n🎯 Test 2: Batch Risk Prediction")
print(f"   GET {API_URL}/risk-prediction/all/")
try:
    response = requests.get(f"{API_URL}/risk-prediction/all/?limit=5", headers=HEADERS)
    if response.status_code == 200:
        data = response.json()
        print(f"   ✅ Status: {response.status_code}")
        print(f"   📋 Projects: {data.get('total_projects', 0)}")
        if data.get('predictions'):
            first = data['predictions'][0]
            print(f"   📌 First Project: {first.get('project_no')}")
            print(f"      Risk Score: {first.get('risk_score')}")
            print(f"      Category: {first.get('risk_category')}")
    else:
        print(f"   ❌ Status: {response.status_code}")
except Exception as e:
    print(f"   ❌ Failed: {str(e)}")

# Test 3: CAR Classification
print("\n📋 Test 3: CAR Classification")
print(f"   POST {API_URL}/car-classification/")
try:
    payload = {
        "car_text": "Material quality does not meet specifications",
        "context": {}
    }
    response = requests.post(f"{API_URL}/car-classification/", 
                            headers=HEADERS, 
                            json=payload)
    if response.status_code == 200:
        data = response.json()
        print(f"   ✅ Status: {response.status_code}")
        print(f"   📂 Category: {data.get('category')}")
        print(f"   🏷️  Label: {data.get('label')}")
        print(f"   ⚠️  Severity: {data.get('severity')}")
        print(f"   ⏱️  Est. Resolution: {data.get('estimated_resolution_days')} days")
    else:
        print(f"   ❌ Status: {response.status_code}")
except Exception as e:
    print(f"   ❌ Failed: {str(e)}")

# Test 4: Manhour Prediction
print("\n⏱️  Test 4: Manhour Prediction")
print(f"   POST {API_URL}/manhour-prediction/")
try:
    payload = {
        "estimated_duration_days": 45,
        "complexity": "moderate",
        "project_type": "Construction"
    }
    response = requests.post(f"{API_URL}/manhour-prediction/", 
                            headers=HEADERS, 
                            json=payload)
    if response.status_code == 200:
        data = response.json()
        print(f"   ✅ Status: {response.status_code}")
        print(f"   📊 Predicted Hours: {data.get('predicted_manhours')}")
        print(f"   📈 With Buffer: {data.get('predicted_with_buffer')}")
        print(f"   🎯 Confidence: {data.get('confidence', 0)*100:.0f}%")
    else:
        print(f"   ❌ Status: {response.status_code}")
except Exception as e:
    print(f"   ❌ Failed: {str(e)}")

# Test 5: Model Status
print("\n🤖 Test 5: AI Models Status")
print(f"   GET {API_URL}/models/status/")
try:
    response = requests.get(f"{API_URL}/models/status/", headers=HEADERS)
    if response.status_code == 200:
        data = response.json()
        print(f"   ✅ Status: {response.status_code}")
        print(f"   📋 Total Models: {data.get('total_models', 0)}")
        print(f"   ✅ Loaded: {data.get('loaded_models', 0)}")
        print(f"   🟢 Enabled: {data.get('enabled_models', 0)}")
    else:
        print(f"   ❌ Status: {response.status_code}")
except Exception as e:
    print(f"   ❌ Failed: {str(e)}")

# Test 6: NLP Remarks Analysis
print("\n📝 Test 6: NLP Remarks Analysis")
print(f"   POST {API_URL}/nlp/analyze-remarks/")
try:
    payload = {
        "remarks_text": "The project quality is excellent but timeline is concerning",
        "analysis_types": ["sentiment", "entities"]
    }
    response = requests.post(f"{API_URL}/nlp/analyze-remarks/", 
                            headers=HEADERS, 
                            json=payload)
    if response.status_code == 200:
        data = response.json()
        print(f"   ✅ Status: {response.status_code}")
        if data.get('sentiment'):
            print(f"   😊 Sentiment: {data['sentiment'].get('label')}")
            print(f"   📊 Score: {data['sentiment'].get('score', 0):.2f}")
    else:
        print(f"   ❌ Status: {response.status_code}")
except Exception as e:
    print(f"   ❌ Failed: {str(e)}")

print("\n" + "=" * 70)
print("✅ API Test Complete!")
print("=" * 70)
print("\n📝 Notes:")
print("   - Update AUTH_TOKEN in this script for authenticated tests")
print("   - Ensure Django server is running")
print("   - Some endpoints require active QHSE projects in database")
print("\n💡 To get auth token:")
print("   1. Login to frontend")
print("   2. Open browser console")
print("   3. Run: localStorage.getItem('radai_access_token')")
print("=" * 70)
