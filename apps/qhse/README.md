"""
QHSE Django App - Complete Database Migration Solution
=======================================================

## Overview
This Django app migrates your Excel-based QHSE data to PostgreSQL database
while maintaining 100% backward compatibility with your existing frontend logic.

## Features
✅ Soft-coded models for easy modifications
✅ RESTful API with filtering and search
✅ Excel import management command
✅ Django admin interface
✅ RBAC integration (module code: 'qhse')
✅ Maintains exact frontend field naming
✅ Comprehensive API endpoints

## Database Models

### 1. QHSERunningProject
Tracks running projects with:
- Project details (number, title, client, managers)
- Timeline (start, close, extension dates)
- Manhours (allocated, used, balance)
- Quality metrics (billability, completion %)
- Audits (project & client audits)
- CARs and Observations
- Performance KPIs

### 2. QHSESpotCheckRegister  
Tracks spot checks with:
- Project reference
- QHSE engineer & date
- Document details
- Comments & findings
- Category (CAR, NCR, Observation, etc.)
- Status tracking

### 3. QHSEAudit
Tracks project audits with:
- Audit type (Project, Client, Internal, External)
- Audit dates and auditor
- Findings and status

## Installation Steps

### 1. Database Migrations
```bash
cd backend
python manage.py makemigrations qhse
python manage.py migrate qhse
```

### 2. Import Excel Data
```bash
# Import running projects only
python manage.py import_qhse_data "/path/to/QHSE Running Projects Data.xlsx" --type projects

# Import spot checks only  
python manage.py import_qhse_data "/path/to/QHSE Running Projects Data.xlsx" --type spot_checks --sheet "Spot Check Register"

# Import both (default)
python manage.py import_qhse_data "/path/to/QHSE Running Projects Data.xlsx" --type both

# Clear existing data before import
python manage.py import_qhse_data "/path/to/QHSE Running Projects Data.xlsx" --clear
```

### 3. Create QHSE Module in RBAC
```python
from apps.rbac.models import Module

# Create QHSE module
qhse_module = Module.objects.create(
    code='qhse',
    name='QHSE Management',
    description='Quality, Health, Safety & Environment Management',
    is_active=True
)

# Assign to users
user.modules.add(qhse_module)
```

## API Endpoints

### Running Projects
```
GET    /api/v1/qhse/projects/                    # List all projects
POST   /api/v1/qhse/projects/                    # Create project
GET    /api/v1/qhse/projects/{id}/               # Get project details
PUT    /api/v1/qhse/projects/{id}/               # Update project
DELETE /api/v1/qhse/projects/{id}/               # Delete project
GET    /api/v1/qhse/projects/dashboard_stats/    # Get dashboard stats
POST   /api/v1/qhse/projects/{id}/duplicate/     # Duplicate project
```

### Spot Check Register
```
GET    /api/v1/qhse/spot-checks/                 # List all spot checks
POST   /api/v1/qhse/spot-checks/                 # Create spot check
GET    /api/v1/qhse/spot-checks/{id}/            # Get spot check details
PUT    /api/v1/qhse/spot-checks/{id}/            # Update spot check
DELETE /api/v1/qhse/spot-checks/{id}/            # Delete spot check
GET    /api/v1/qhse/spot-checks/by_project/      # Group by project
```

### Audits
```
GET    /api/v1/qhse/audits/                      # List all audits
POST   /api/v1/qhse/audits/                      # Create audit
GET    /api/v1/qhse/audits/{id}/                 # Get audit details
PUT    /api/v1/qhse/audits/{id}/                 # Update audit
DELETE /api/v1/qhse/audits/{id}/                 # Delete audit
```

## Query Parameters (Filtering)

### Running Projects
```
?client=ADNOC                          # Filter by client
?project_manager=Pankaj                # Filter by PM
?quality_engineer=Rajasekhar           # Filter by QE
?overdue=true                          # Only overdue projects
?start_date=2024-01-01                 # Projects starting after date
?end_date=2024-12-31                   # Projects ending before date
?search=ADNOC                          # Search in multiple fields
?ordering=-updated_at                  # Sort by field (- for desc)
```

### Spot Checks
```
?project_no=5900738                    # Filter by project
?engineer=Rajasekhar                   # Filter by engineer
?status=OPEN                           # Filter by status
?category=CAR                          # Filter by category
?start_date=2024-01-01                 # Checks after date
?end_date=2024-12-31                   # Checks before date
```

## Frontend Integration

### Replace Google Sheets API with Django API

#### Old Code (Google Sheets):
```javascript
const fetchGoogleSheet = async ({ sheetId, sheetName, apiKey }) => {
  const url = `https://sheets.googleapis.com/v4/spreadsheets/${sheetId}/values/${sheetName}?key=${apiKey}`;
  const response = await fetch(url);
  return response.json();
};
```

#### New Code (Django API):
```javascript
import { API_BASE_URL } from '../../config/api.config';

const fetchQHSEProjects = async () => {
  const token = localStorage.getItem('radai_access_token');
  const response = await fetch(`${API_BASE_URL}/qhse/projects/`, {
    headers: {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json'
    }
  });
  return response.json();
};
```

### Example: Update use-qhse-running-projects.js
```javascript
import { useEffect, useState, useCallback } from 'react';
import { API_BASE_URL } from '../../config/api.config';

export function useQHSERunningProjects(pollInterval = 3600000) {
  const [data, setData] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchProjects = useCallback(async () => {
    try {
      const token = localStorage.getItem('radai_access_token');
      const response = await fetch(`${API_BASE_URL}/qhse/projects/`, {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });
      
      if (!response.ok) throw new Error('Failed to fetch');
      
      const projects = await response.json();
      setData(projects);
      setError(null);
    } catch (err) {
      setError(err.message);
      setData([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchProjects();
    const interval = setInterval(fetchProjects, pollInterval);
    return () => clearInterval(interval);
  }, [fetchProjects, pollInterval]);

  return { data, loading, error, refetch: fetchProjects };
}
```

## Dashboard Statistics

The API provides comprehensive statistics:

```javascript
const stats = await fetch(`${API_BASE_URL}/qhse/projects/dashboard_stats/`, {
  headers: { 'Authorization': `Bearer ${token}` }
}).then(r => r.json());

// Returns:
{
  total_projects: 15,
  active_projects: 12,
  overdue_projects: 2,
  total_cars_open: 7,
  total_cars_closed: 24,
  total_obs_open: 18,
  total_obs_closed: 45,
  total_spot_checks: 156,
  pending_spot_checks: 23,
  average_quality_billability: 94.5,
  average_project_completion: 87.3,
  total_manhours_allocated: 1250.0,
  total_manhours_used: 1180.5,
  total_audits_completed: 32,
  projects_by_client: {
    "ADNOC GAS": 8,
    "Al Fanar": 4,
    ...
  },
  monthly_spot_checks: {
    "2024-01": 12,
    "2024-02": 15,
    ...
  }
}
```

## Advantages Over Excel/Google Sheets

1. **Performance**: Direct database queries vs API calls
2. **Real-time**: Immediate updates, no polling delays
3. **Security**: JWT authentication, RBAC integration
4. **Scalability**: Handles thousands of records efficiently
5. **Relationships**: Foreign keys, proper data integrity
6. **Advanced Queries**: Complex filtering, aggregations
7. **Backup**: PostgreSQL automated backups
8. **Audit Trail**: Track who created/modified data
9. **Offline Capability**: Can work with cached data
10. **No API Limits**: No Google Sheets API quotas

## Maintenance

### Add New Fields (Soft-coded)
1. Add field to model in `models.py`
2. Add to serializer in `serializers.py`
3. Run migrations
4. Update frontend if needed

### Data Sync
```bash
# Regular data imports
python manage.py import_qhse_data "/path/to/latest.xlsx"

# Or create a cron job/scheduled task
```

### Backup Database
```bash
# Export to SQL
pg_dump radai_db > qhse_backup.sql

# Export to Excel (create custom command)
python manage.py export_qhse_data "export.xlsx"
```

## Troubleshooting

### Import Errors
- Check Excel column names match FIELD_MAPPING
- Verify date formats (YYYY-MM-DD or DD.MM.YYYY)
- Check for special characters in data
- Review logs for specific row errors

### API Errors
- Verify user has 'qhse' module access
- Check authentication token is valid
- Ensure API_BASE_URL is correct
- Check CORS settings for frontend domain

### Performance Issues
- Add database indexes (already included)
- Use pagination for large datasets
- Cache dashboard stats
- Use select_related/prefetch_related for queries

## Support & Customization

The entire system is soft-coded and can be easily customized:
- Modify field mappings in serializers
- Add new API endpoints in views
- Extend models with new fields
- Create custom management commands
- Add computed properties for business logic

## Security

- ✅ JWT Authentication required
- ✅ RBAC module-based access control
- ✅ User tracking (created_by, updated_by)
- ✅ Soft delete support (is_active flag)
- ✅ Input validation via serializers
- ✅ SQL injection protection (Django ORM)
- ✅ CORS configuration for frontend access

---

**Author**: GitHub Copilot AI Assistant
**Date**: January 15, 2026
**Version**: 1.0.0
