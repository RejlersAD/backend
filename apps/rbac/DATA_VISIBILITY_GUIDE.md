# 🔐 Data Visibility & Row-Level Security Implementation Guide

**Date:** January 22, 2026  
**System:** RADAI Application  
**Challenge:** Users can see other users' data despite role-based access control

---

## 📋 Table of Contents

1. [Problem Statement](#problem-statement)
2. [Solution Overview](#solution-overview)
3. [Architecture](#architecture)
4. [Quick Start](#quick-start)
5. [Implementation Steps](#implementation-steps)
6. [Module-Specific Examples](#module-specific-examples)
7. [Testing](#testing)
8. [Best Practices](#best-practices)

---

## 🎯 Problem Statement

### Current Situation
- ✅ **RBAC Works**: Controls which modules (CRS, QHSE, Finance) users can access
- ❌ **No Row-Level Security**: Users can see ALL data within allowed modules
- ❌ **No Department Collaboration**: QHSE team can't see each other's work

### Example Issues

**Issue 1: CRS Documents**
```python
# Current code (INSECURE)
class CRSDocumentViewSet(viewsets.ModelViewSet):
    queryset = CRSDocument.objects.all()  # ❌ Returns ALL documents
    
# Result: Any user with CRS access sees EVERYONE's documents
```

**Issue 2: QHSE Projects**
```python
# Current code (NO COLLABORATION)
class QHSERunningProjectViewSet(viewsets.ModelViewSet):
    queryset = QHSERunningProject.objects.all()  # ❌ Returns ALL projects
    
# Result: QHSE team members can't collaborate effectively
```

---

## 💡 Solution Overview

### Three-Layer Security Model

```
┌─────────────────────────────────────────────────┐
│ Layer 1: RBAC (Existing ✅)                     │
│ - Controls MODULE access                        │
│ - Who can access CRS, QHSE, Finance, etc.      │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ Layer 2: Data Visibility (NEW 🆕)               │
│ - Controls ROW-LEVEL access                     │
│ - Who can see which specific records           │
└─────────────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────────────┐
│ Layer 3: Field-Level Permissions (Optional)     │
│ - Controls FIELD access                         │
│ - Who can see sensitive fields (salary, etc.)  │
└─────────────────────────────────────────────────┘
```

### Visibility Strategies

| Strategy | Description | Use Case |
|----------|-------------|----------|
| **PERSONAL** | User sees only their own data | Notifications, personal files |
| **MODULE_TEAM** | Users with same module see each other's data | QHSE, CRS, Finance (collaboration) |
| **ORGANIZATION** | All users in org see data | File storage, shared resources |
| **CUSTOM** | Custom logic | Complex scenarios (projects) |

---

## 🏗️ Architecture

### Files Created

```
backend/apps/rbac/
├── data_visibility_config.py    # Configuration & helper functions
├── data_visibility_mixin.py     # Reusable ViewSet mixins
└── DATA_VISIBILITY_GUIDE.md     # This file
```

### Configuration Structure

```python
# data_visibility_config.py

DATA_VISIBILITY_CONFIG = {
    'crs_documents': {
        'strategy': 'MODULE_TEAM',         # Team collaboration
        'module_code': 'crs_documents',    # Module to check
        'owner_field': 'uploaded_by',      # Who owns the record
        'description': 'CRS users see all CRS documents',
    },
    'qhse': {
        'strategy': 'MODULE_TEAM',
        'module_code': 'qhse',
        'owner_field': None,               # Team-based (no owner)
        'description': 'QHSE team sees all QHSE projects',
    },
}
```

---

## 🚀 Quick Start

### Step 1: Choose Your Approach

**Option A: Use Mixin (Recommended)**
```python
from apps.rbac.data_visibility_mixin import TeamCollaborationMixin

class CRSDocumentViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    visibility_module_code = 'crs_documents'
    visibility_owner_field = 'uploaded_by'
    # Done! Automatic filtering applied
```

**Option B: Manual Implementation**
```python
from apps.rbac.data_visibility_config import build_visibility_filter

class CRSDocumentViewSet(viewsets.ModelViewSet):
    def get_queryset(self):
        queryset = super().get_queryset()
        filter_q = build_visibility_filter(
            user=self.request.user,
            module_code='crs_documents',
            owner_field='uploaded_by'
        )
        return queryset.filter(filter_q)
```

---

## 📝 Implementation Steps

### Step 1: Configure Module Visibility

Edit `backend/apps/rbac/data_visibility_config.py`:

```python
DATA_VISIBILITY_CONFIG = {
    'your_module': {
        'strategy': VisibilityStrategy.MODULE_TEAM,
        'module_code': 'your_module',
        'owner_field': 'created_by',  # or uploaded_by, owner, etc.
        'description': 'Team members can collaborate',
    },
}
```

### Step 2: Update Your ViewSet

**Before:**
```python
class MyViewSet(viewsets.ModelViewSet):
    queryset = MyModel.objects.all()  # ❌ Insecure
    serializer_class = MySerializer
    permission_classes = [IsAuthenticated]
```

**After:**
```python
from apps.rbac.data_visibility_mixin import TeamCollaborationMixin

class MyViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    # Add these two lines
    visibility_module_code = 'your_module'
    visibility_owner_field = 'created_by'
    
    queryset = MyModel.objects.all()
    serializer_class = MySerializer
    permission_classes = [IsAuthenticated]
```

### Step 3: Test

```bash
# Test as different users
curl -H "Authorization: Bearer <user1_token>" http://localhost:8000/api/v1/your-endpoint/
curl -H "Authorization: Bearer <user2_token>" http://localhost:8000/api/v1/your-endpoint/

# Verify:
# - Users see only appropriate data
# - Team members see each other's data
# - Admins see everything
```

---

## 🎨 Module-Specific Examples

### Example 1: CRS Documents (Team Collaboration)

**Requirement:** CRS team members should see all CRS documents to collaborate

**Implementation:**
```python
# backend/apps/crs/views.py

from apps.rbac.data_visibility_mixin import TeamCollaborationMixin

class CRSDocumentViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    """
    CRS Document ViewSet with team collaboration
    - CRS team members see all CRS documents
    - Non-CRS users see only their own documents
    - Admins see everything
    """
    visibility_module_code = 'crs_documents'
    visibility_owner_field = 'uploaded_by'
    
    queryset = CRSDocument.objects.all()
    serializer_class = CRSDocumentSerializer
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action == 'retrieve':
            return CRSDocumentDetailSerializer
        return CRSDocumentSerializer
    
    def perform_create(self, serializer):
        serializer.save(uploaded_by=self.request.user)
```

**Behavior:**
- User with `crs_documents` module → Sees all CRS documents
- User without `crs_documents` module → Sees only their own documents
- Super Admin → Sees all documents

---

### Example 2: QHSE Projects (Team Collaboration, No Owner)

**Requirement:** All QHSE team members see all QHSE projects (no personal ownership)

**Implementation:**
```python
# backend/apps/qhse/views.py

from apps.rbac.data_visibility_mixin import TeamCollaborationMixin

class QHSERunningProjectViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    """
    QHSE Projects ViewSet with full team visibility
    - All QHSE team members see all projects
    - Non-QHSE users see nothing (no personal fallback)
    - Admins see everything
    """
    visibility_module_code = 'qhse'
    # No visibility_owner_field - team-based only
    
    queryset = QHSERunningProject.objects.filter(is_active=True)
    serializer_class = QHSERunningProjectSerializer
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['get'])
    def team_members(self, request):
        """Get all QHSE team members for assignment"""
        team = self.get_team_members_queryset()
        return Response([
            {'id': u.id, 'email': u.email, 'name': f"{u.first_name} {u.last_name}"}
            for u in team
        ])
```

**Behavior:**
- User with `qhse` module → Sees all QHSE projects
- User without `qhse` module → Sees nothing (no access)
- Super Admin → Sees all projects

---

### Example 3: Notifications (Personal Only)

**Requirement:** Users see only their own notifications

**Implementation:**
```python
# backend/apps/notifications/views.py

from apps.rbac.data_visibility_mixin import PersonalDataMixin

class NotificationViewSet(PersonalDataMixin, viewsets.ModelViewSet):
    """
    Notifications ViewSet with personal visibility only
    - Users see only their own notifications
    - No team sharing
    - Admins see all notifications
    """
    visibility_owner_field = 'recipient'
    
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]
```

**Behavior:**
- Regular user → Sees only notifications addressed to them
- Super Admin → Sees all notifications

---

### Example 4: Projects (Custom Logic)

**Requirement:** Users see projects they own OR are members of

**Implementation:**
```python
# backend/apps/core/project_views.py

from apps.rbac.data_visibility_mixin import ProjectBasedMixin

class ProjectViewSet(ProjectBasedMixin, viewsets.ModelViewSet):
    """
    Projects ViewSet with owner + member visibility
    - Users see projects they own
    - Users see projects they are members of
    - Admins see all projects
    """
    project_owner_field = 'owner'
    project_members_field = 'team_members'
    
    queryset = Project.objects.filter(is_deleted=False)
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated]
```

**Behavior:**
- User A owns Project 1 → Sees Project 1
- User B is member of Project 1 → Sees Project 1
- User C (not involved) → Doesn't see Project 1
- Super Admin → Sees all projects

---

### Example 5: Design Projects (Public + Private)

**Requirement:** Show public templates + user's private templates

**Implementation:**
```python
# backend/apps/designiq/views.py

from apps.rbac.data_visibility_mixin import CustomVisibilityMixin
from django.db.models import Q

class DesignTemplateViewSet(CustomVisibilityMixin, viewsets.ModelViewSet):
    """
    Design Templates with custom visibility
    - Public templates visible to everyone
    - Private templates visible only to creator
    - Admins see all templates
    """
    visibility_module_code = 'designiq'
    visibility_owner_field = 'created_by'
    
    queryset = DesignTemplate.objects.all()
    serializer_class = DesignTemplateSerializer
    permission_classes = [IsAuthenticated]
    
    def get_visibility_filter(self):
        """Custom logic: public + personal private"""
        from apps.rbac.data_visibility_config import is_admin_user
        
        # Admins see everything
        if is_admin_user(self.request.user):
            return Q()
        
        # Others see public + their own
        return Q(is_public=True) | Q(created_by=self.request.user)
```

---

## 🧪 Testing

### Test Scenarios

**Test 1: Team Collaboration (QHSE)**
```python
# backend/test_qhse_visibility.py

from django.test import TestCase
from django.contrib.auth import get_user_model
from apps.qhse.models import QHSERunningProject
from apps.rbac.models import UserProfile, Role, Module, RoleModule

User = get_user_model()

class QHSEVisibilityTestCase(TestCase):
    def setUp(self):
        # Create users
        self.qhse_user1 = User.objects.create_user('qhse1@test.com', password='test')
        self.qhse_user2 = User.objects.create_user('qhse2@test.com', password='test')
        self.other_user = User.objects.create_user('other@test.com', password='test')
        
        # Create QHSE module and role
        qhse_module = Module.objects.create(code='qhse', name='QHSE')
        qhse_role = Role.objects.create(code='qhse_role', name='QHSE Role')
        RoleModule.objects.create(role=qhse_role, module=qhse_module)
        
        # Assign QHSE module to users 1 and 2
        for user in [self.qhse_user1, self.qhse_user2]:
            profile = UserProfile.objects.create(user=user)
            profile.roles.add(qhse_role)
        
        # Create test project
        self.project = QHSERunningProject.objects.create(
            sr_no=1,
            project_no='QHSE-001',
            project_title='Test Project',
            client='Test Client'
        )
    
    def test_qhse_users_see_all_projects(self):
        """QHSE team members should see all QHSE projects"""
        from apps.rbac.data_visibility_config import build_visibility_filter
        from apps.qhse.models import QHSERunningProject
        
        for user in [self.qhse_user1, self.qhse_user2]:
            queryset = QHSERunningProject.objects.all()
            filter_q = build_visibility_filter(
                user=user,
                module_code='qhse'
            )
            filtered = queryset.filter(filter_q)
            
            self.assertEqual(filtered.count(), 1, 
                f"QHSE user {user.email} should see 1 project")
            self.assertIn(self.project, filtered)
    
    def test_non_qhse_user_sees_nothing(self):
        """Non-QHSE users should not see QHSE projects"""
        from apps.rbac.data_visibility_config import build_visibility_filter
        
        queryset = QHSERunningProject.objects.all()
        filter_q = build_visibility_filter(
            user=self.other_user,
            module_code='qhse'
        )
        filtered = queryset.filter(filter_q)
        
        self.assertEqual(filtered.count(), 0,
            "Non-QHSE user should see 0 projects")
```

**Run tests:**
```bash
python manage.py test backend.test_qhse_visibility
```

---

### Manual Testing Steps

**Step 1: Create Test Users**
```python
# backend/create_test_users.py

from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Role, Module, RoleModule, UserRole

User = get_user_model()

# Create QHSE users
qhse_user1 = User.objects.create_user('qhse_user1@test.com', password='Test123!')
qhse_user2 = User.objects.create_user('qhse_user2@test.com', password='Test123!')

# Create regular user (no QHSE access)
regular_user = User.objects.create_user('regular@test.com', password='Test123!')

# Assign QHSE module to users 1 and 2
qhse_module = Module.objects.get(code='qhse')
qhse_role = Role.objects.get_or_create(code='qhse_team', name='QHSE Team')[0]
RoleModule.objects.get_or_create(role=qhse_role, module=qhse_module)

for user in [qhse_user1, qhse_user2]:
    profile = UserProfile.objects.get_or_create(user=user)[0]
    UserRole.objects.get_or_create(profile=profile, role=qhse_role)

print("✅ Test users created")
```

**Step 2: Test API Access**
```bash
# Login as QHSE User 1
curl -X POST http://localhost:8000/api/v1/auth/login/ \
  -d '{"email": "qhse_user1@test.com", "password": "Test123!"}'

# Get projects (should see all QHSE projects)
curl -H "Authorization: Bearer <token>" \
  http://localhost:8000/api/v1/qhse/running-projects/

# Login as Regular User
curl -X POST http://localhost:8000/api/v1/auth/login/ \
  -d '{"email": "regular@test.com", "password": "Test123!"}'

# Get projects (should see nothing or 403 error)
curl -H "Authorization: Bearer <token>" \
  http://localhost:8000/api/v1/qhse/running-projects/
```

---

## 📚 Best Practices

### 1. Always Set Module Code
```python
# ✅ GOOD
class MyViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    visibility_module_code = 'my_module'  # Required
    
# ❌ BAD
class MyViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    # Missing visibility_module_code - falls back to insecure behavior
```

### 2. Choose Correct Strategy

| Scenario | Strategy | Example |
|----------|----------|---------|
| Team needs to collaborate | `MODULE_TEAM` | QHSE, CRS, Finance |
| Personal data only | `PERSONAL` | Notifications, preferences |
| Org-wide sharing | `ORGANIZATION` | File storage |
| Complex rules | `CUSTOM` | Projects (owner + members) |

### 3. Test with Different User Types

Always test with:
- ✅ User with module access (team member)
- ✅ User without module access
- ✅ Super Admin
- ✅ Regular admin

### 4. Audit Logging

Enable audit logging for compliance:
```python
class MyViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    visibility_logging = True  # Default: enabled
```

### 5. Performance Considerations

```python
# ✅ GOOD: Use select_related/prefetch_related
class CRSDocumentViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.select_related('uploaded_by').prefetch_related('comments')

# ❌ BAD: N+1 query problems
class CRSDocumentViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    queryset = CRSDocument.objects.all()  # Missing optimizations
```

---

## 🔄 Migration Plan

### Phase 1: High-Priority Modules (Week 1)
- ✅ CRS Documents
- ✅ QHSE Projects
- ✅ Finance Records

### Phase 2: Medium-Priority (Week 2)
- ✅ PFD Converter
- ✅ PID Analysis
- ✅ Procurement

### Phase 3: Low-Priority (Week 3)
- ✅ File Storage
- ✅ Notifications
- ✅ Audit Logs

### Rollback Plan
If issues occur:
```python
# Temporarily bypass filtering
class MyViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    visibility_bypass = True  # ⚠️ Emergency use only
```

---

## 📞 Support & Questions

### Common Issues

**Issue 1: Users can't see any data**
```python
# Check module assignment
from apps.rbac.data_visibility_config import user_has_module_access

user = User.objects.get(email='user@example.com')
has_access = user_has_module_access(user, 'qhse')
print(f"Has QHSE access: {has_access}")
```

**Issue 2: Admins can't see everything**
```python
# Check admin status
from apps.rbac.data_visibility_config import is_admin_user

user = User.objects.get(email='admin@example.com')
is_admin = is_admin_user(user)
print(f"Is admin: {is_admin}")
```

**Issue 3: Performance problems**
```python
# Enable query debugging
from django.db import connection

# After your queryset operation
print(f"Query count: {len(connection.queries)}")
for query in connection.queries:
    print(query['sql'])
```

---

## ✅ Checklist

Before deploying to production:

- [ ] All modules have visibility configuration
- [ ] All ViewSets use appropriate mixin
- [ ] Test scenarios passed
- [ ] Admin access verified
- [ ] Team collaboration verified
- [ ] Audit logging enabled
- [ ] Performance optimized (select_related/prefetch_related)
- [ ] Documentation updated
- [ ] Frontend updated (if showing user lists)
- [ ] Backup database before deployment

---

## 📄 Quick Reference

### Import Statements
```python
# Configuration
from apps.rbac.data_visibility_config import (
    build_visibility_filter,
    is_admin_user,
    user_has_module_access,
    get_users_with_module_access,
)

# Mixins
from apps.rbac.data_visibility_mixin import (
    DataVisibilityMixin,
    TeamCollaborationMixin,
    PersonalDataMixin,
    ProjectBasedMixin,
    CustomVisibilityMixin,
)
```

### Mixin Attributes
```python
visibility_module_code = 'your_module'      # Required
visibility_owner_field = 'created_by'       # Optional
visibility_additional_filters = Q(...)      # Optional
visibility_bypass = False                   # Use with caution
visibility_logging = True                   # Audit trail
```

---

**Last Updated:** January 22, 2026  
**Version:** 1.0  
**Status:** ✅ Ready for Implementation
