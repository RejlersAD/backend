# 🚀 Backend Container - Quick Start Guide

## ✅ Current Status
**All containers are running and fully operational!**

### Running Containers
- ✅ **aiflow_backend** - Django API (Port 8000)
- ✅ **aiflow_redis** - Cache & Message Broker (Port 6379)
- ✅ **aiflow_postgres** - Database (Port 5432)

---

## 🎯 Available Test Scripts

### 1. Basic Health Check
```powershell
.\test_backend.ps1
```
Tests: Health endpoint, CORS, API docs, authentication endpoint existence

### 2. Comprehensive Test (Recommended)
```powershell
.\test_complete.ps1
```
Tests: All health checks + creates test user + full authentication flow

### 3. API Endpoints Test
```powershell
.\test_api_endpoints.ps1
```
Tests: All major API endpoints with authentication

### 4. Module Access Test
```powershell
.\test_modules.ps1
```
Tests: All feature modules (PID, PFD, CRS, Finance) with authentication

---

## 🔑 Test Credentials

**Email:** test@radai.ae  
**Password:** testpass123

**Admin Credentials:**  
**Email:** admin@rejlers.com  
**Username:** Tanzeem@123

---

## 📡 Key API Endpoints

### Authentication
```
POST /api/v1/auth/login/
POST /api/v1/auth/refresh/
POST /api/v1/auth/verify-email/
```

### Health & Monitoring
```
GET /api/v1/health/
GET /api/v1/health/diagnostic/
GET /api/v1/cors-test/
```

### Features
```
GET /api/v1/features/
GET /api/v1/features/{feature_id}/
```

### PID Analysis
```
GET /api/v1/pid/drawings/
POST /api/v1/pid/upload/
```

### PFD Converter
```
GET /api/v1/pfd/conversions/
POST /api/v1/pfd/upload/
```

### CRS Documents
```
GET /api/v1/crs/documents/
POST /api/v1/crs/upload/
```

### Finance Module
```
GET /api/v1/finance/invoices/
POST /api/v1/finance/upload/
GET /api/v1/finance/invoices/{id}/
```

### User Management
```
GET /api/v1/users/
GET /api/v1/users/me/
PUT /api/v1/users/{id}/
```

---

## 🔐 Authentication Flow

### 1. Login
```powershell
$body = @{
    email = "test@radai.ae"
    password = "testpass123"
} | ConvertTo-Json

$response = Invoke-RestMethod `
    -Uri "http://localhost:8000/api/v1/auth/login/" `
    -Method Post `
    -Body $body `
    -ContentType "application/json"

$token = $response.access
```

### 2. Use Token
```powershell
$headers = @{
    "Authorization" = "Bearer $token"
}

$data = Invoke-RestMethod `
    -Uri "http://localhost:8000/api/v1/features/" `
    -Headers $headers
```

---

## 📊 Current System Data

- **Users:** 41 registered users
- **Finance Invoices:** 31 invoices
- **PID Drawings:** 0
- **PFD Conversions:** 0
- **CRS Documents:** 0

---

## 🐳 Docker Commands

### View Container Status
```powershell
docker-compose ps
```

### View Logs
```powershell
# All containers
docker-compose logs -f

# Specific container
docker-compose logs -f backend
docker-compose logs -f redis
docker-compose logs -f db
```

### Restart Containers
```powershell
docker-compose restart
```

### Stop Containers
```powershell
docker-compose stop
```

### Stop and Remove
```powershell
docker-compose down
```

### Rebuild and Start
```powershell
docker-compose up -d --build
```

### Execute Commands in Container
```powershell
# Django shell
docker exec -it aiflow_backend python manage.py shell

# Django migrations
docker exec -it aiflow_backend python manage.py migrate

# Create superuser
docker exec -it aiflow_backend python manage.py createsuperuser

# Collect static files
docker exec -it aiflow_backend python manage.py collectstatic
```

---

## 🌐 Web Interfaces

### API Documentation (Swagger)
```
http://localhost:8000/api/docs/
```

### Django Admin Panel
```
http://localhost:8000/admin/
```

### Health Check
```
http://localhost:8000/api/v1/health/
```

---

## 📝 Manual Testing with Postman/Insomnia

### 1. Login Request
**POST** `http://localhost:8000/api/v1/auth/login/`

Headers:
```
Content-Type: application/json
```

Body:
```json
{
  "email": "test@radai.ae",
  "password": "testpass123"
}
```

### 2. Get Features (Authenticated)
**GET** `http://localhost:8000/api/v1/features/`

Headers:
```
Authorization: Bearer YOUR_ACCESS_TOKEN
```

### 3. Upload File (Example)
**POST** `http://localhost:8000/api/v1/pid/upload/`

Headers:
```
Authorization: Bearer YOUR_ACCESS_TOKEN
Content-Type: multipart/form-data
```

Body:
```
file: [Select File]
```

---

## 🔍 Troubleshooting

### Container Not Starting
```powershell
# Check logs
docker-compose logs backend

# Check if ports are in use
netstat -ano | findstr :8000
```

### Database Issues
```powershell
# Check database connection
docker exec aiflow_backend python manage.py check --database default

# Run migrations
docker exec aiflow_backend python manage.py migrate
```

### Redis Issues
```powershell
# Check Redis connection
docker exec aiflow_redis redis-cli ping
```

### Permission Issues
```powershell
# Check container user
docker exec aiflow_backend whoami

# Check file permissions
docker exec aiflow_backend ls -la /app
```

---

## 📦 Environment Variables

Key environment variables (from `.env` file):
- `DEBUG=True`
- `DATABASE_URL` - PostgreSQL connection (Railway)
- `REDIS_URL` - Redis connection
- `SECRET_KEY` - Django secret key
- `ALLOWED_HOSTS` - Allowed hosts
- `CORS_ALLOWED_ORIGINS` - CORS configuration
- `OPENAI_API_KEY` - OpenAI API key for AI features
- `AWS_*` - AWS S3 configuration
- `EMAIL_*` - SMTP email configuration

---

## 🎯 Next Steps

1. ✅ **Backend is ready for testing**
2. Test file uploads through Postman/Insomnia
3. Integrate with frontend application
4. Test end-to-end workflows
5. Configure production environment

---

## 📞 Support Resources

- **API Documentation:** http://localhost:8000/api/docs/
- **Container Logs:** `docker-compose logs -f backend`
- **Django Shell:** `docker exec -it aiflow_backend python manage.py shell`
- **Health Check:** http://localhost:8000/api/v1/health/

---

## ✨ Quick Test Commands

```powershell
# Test health
curl http://localhost:8000/api/v1/health/

# Run all tests
.\test_complete.ps1

# Test modules
.\test_modules.ps1

# View backend logs
docker-compose logs -f backend
```

---

**Status:** 🟢 All systems operational  
**Last Updated:** January 8, 2026  
**Version:** 2.1.0
