"""
Smart Feature Registry System
Allows dynamic addition of features without modifying core logic
"""

from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
from enum import Enum


class FeatureCategory(Enum):
    """Feature categories for organization"""
    ENGINEERING = "engineering"
    DOCUMENT_MANAGEMENT = "document_management"
    QUALITY_ASSURANCE = "quality_assurance"
    SAFETY = "safety"
    COMPLIANCE = "compliance"
    OPERATIONS = "operations"
    ANALYTICS = "analytics"
    OTHER = "other"


class FeatureStatus(Enum):
    """Feature deployment status"""
    ACTIVE = "active"
    BETA = "beta"
    COMING_SOON = "coming_soon"
    MAINTENANCE = "maintenance"
    DEPRECATED = "deprecated"


@dataclass
class FeatureConfig:
    """Configuration for a single feature"""
    
    # Basic Info (Required fields first)
    id: str  # Unique identifier (e.g., 'pid_analysis', 'crs_documents')
    name: str  # Display name
    description: str  # Short description
    icon: str  # Icon identifier or emoji
    category: FeatureCategory
    frontend_route: str  # e.g., '/pid/upload'
    backend_url_pattern: str  # e.g., 'api/pid/'
    
    # Optional fields with defaults
    status: FeatureStatus = FeatureStatus.ACTIVE
    
    # UI Configuration
    color_scheme: Dict[str, str] = field(default_factory=lambda: {
        "primary": "blue",
        "secondary": "indigo"
    })
    
    # Permissions
    required_permissions: List[str] = field(default_factory=list)
    department_access: List[str] = field(default_factory=list)  # Empty = all departments
    
    # Features & Capabilities
    capabilities: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)  # For search
    
    # Integration Points
    app_name: str = ""  # Django app name
    has_upload: bool = False
    has_report: bool = False
    has_dashboard: bool = False
    has_export: bool = False
    
    # Additional metadata
    order: int = 0  # Display order
    is_new: bool = False
    version: str = "1.0.0"
    documentation_url: Optional[str] = None
    
    def to_dict(self) -> Dict:
        """Convert to dictionary for API responses"""
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "category": self.category.value,
            "status": self.status.value,
            "frontendRoute": self.frontend_route,
            "backendUrl": self.backend_url_pattern,
            "colorScheme": self.color_scheme,
            "requiredPermissions": self.required_permissions,
            "departmentAccess": self.department_access,
            "capabilities": self.capabilities,
            "keywords": self.keywords,
            "hasUpload": self.has_upload,
            "hasReport": self.has_report,
            "hasDashboard": self.has_dashboard,
            "hasExport": self.has_export,
            "order": self.order,
            "isNew": self.is_new,
            "version": self.version,
            "documentationUrl": self.documentation_url,
        }


class FeatureRegistry:
    """
    Central registry for all features in the system
    Implements singleton pattern for global access
    """
    
    _instance = None
    _features: Dict[str, FeatureConfig] = {}
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(FeatureRegistry, cls).__new__(cls)
            cls._instance._initialize_features()
        return cls._instance
    
    def _initialize_features(self):
        """Initialize with existing features - called once"""
        
        # PID Analysis Feature
        self.register(FeatureConfig(
            id="pid_analysis",
            name="P&ID Design Verification",
            description="AI-powered engineering review for oil & gas P&ID drawings",
            icon="📋",
            category=FeatureCategory.ENGINEERING,
            frontend_route="/pid/upload",
            backend_url_pattern="api/pid/",
            color_scheme={
                "primary": "blue",
                "secondary": "indigo",
                "gradient": "from-blue-500 to-indigo-600"
            },
            capabilities=[
                "Equipment & instrumentation verification",
                "Safety systems & PSV isolation compliance",
                "ADNOC / DEP / API / ISA standard checks"
            ],
            keywords=["pid", "piping", "instrumentation", "diagram", "engineering"],
            app_name="pid_analysis",
            has_upload=True,
            has_report=True,
            has_export=True,
            order=1,
            version="2.1.0"
        ))
        
        # PFD Converter Feature
        self.register(FeatureConfig(
            id="pfd_converter",
            name="PFD to P&ID Converter",
            description="AI-powered intelligent conversion from Process Flow Diagrams to detailed P&IDs",
            icon="🔄",
            category=FeatureCategory.ENGINEERING,
            status=FeatureStatus.ACTIVE,
            frontend_route="/pfd/upload",
            backend_url_pattern="api/pfd/",
            color_scheme={
                "primary": "purple",
                "secondary": "pink",
                "gradient": "from-purple-500 to-pink-600"
            },
            capabilities=[
                "Auto-generate instrumentation & control loops",
                "Intelligent piping & valve specifications",
                "Standards-compliant safety systems (PSVs, ESD)",
                "ADNOC DEP & API standard compliance"
            ],
            keywords=["pfd", "converter", "process flow", "diagram"],
            app_name="pfd_converter",
            has_upload=True,
            has_report=True,
            order=2,
            is_new=True,
            version="1.0.0"
        ))
        
        # CRS Documents Feature (NEW - Example)
        self.register(FeatureConfig(
            id="crs_documents",
            name="CRS Document Management",
            description="Centralized repository for Company Required Specifications documents",
            icon="📑",
            category=FeatureCategory.DOCUMENT_MANAGEMENT,
            status=FeatureStatus.ACTIVE,
            frontend_route="/crs/documents",
            backend_url_pattern="api/crs/",
            color_scheme={
                "primary": "emerald",
                "secondary": "teal",
                "gradient": "from-emerald-500 to-teal-600"
            },
            capabilities=[
                "Document version control & tracking",
                "Automated compliance verification",
                "Multi-department access management",
                "AI-powered document search & retrieval"
            ],
            keywords=["crs", "documents", "specifications", "compliance"],
            app_name="crs_documents",
            has_upload=True,
            has_dashboard=True,
            has_export=True,
            # No permissions required - accessible to all authenticated users
            # required_permissions=["view_crs_documents"],
            order=3,
            is_new=True,
            version="1.0.0"
        ))
        
        # Admin Dashboard Feature (Super Admin Only)
        self.register(FeatureConfig(
            id="admin_dashboard",
            name="Admin Dashboard",
            description="System overview, analytics, and configuration management",
            icon="⚙️",
            category=FeatureCategory.OTHER,
            status=FeatureStatus.ACTIVE,
            frontend_route="/admin",
            backend_url_pattern="api/admin/",
            color_scheme={
                "primary": "amber",
                "secondary": "orange",
                "gradient": "from-amber-500 to-orange-600"
            },
            capabilities=[
                "System health monitoring",
                "Feature usage analytics",
                "Global configuration management",
                "Audit logs & activity tracking"
            ],
            keywords=["admin", "dashboard", "system", "analytics"],
            app_name="rbac",  # Uses existing RBAC app
            has_dashboard=True,
            required_permissions=["users.view_admin_dashboard"],
            order=10,
            version="1.0.0"
        ))
        
        # User Management Feature (Super Admin Only)
        self.register(FeatureConfig(
            id="user_management",
            name="User Management",
            description="Comprehensive user, role, and permission management",
            icon="👥",
            category=FeatureCategory.OTHER,
            status=FeatureStatus.ACTIVE,
            frontend_route="/admin/users",
            backend_url_pattern="api/users/",
            color_scheme={
                "primary": "violet",
                "secondary": "purple",
                "gradient": "from-violet-500 to-purple-600"
            },
            capabilities=[
                "Create & manage user accounts",
                "Role-based access control (RBAC)",
                "Permission assignment & revocation",
                "Department & team management"
            ],
            keywords=["users", "permissions", "roles", "rbac", "management"],
            app_name="users",
            has_dashboard=True,
            required_permissions=["users.view_user_management"],
            order=11,
            version="1.0.0"
        ))
    
    def register(self, feature: FeatureConfig):
        """Register a new feature"""
        self._features[feature.id] = feature
    
    def unregister(self, feature_id: str):
        """Unregister a feature"""
        if feature_id in self._features:
            del self._features[feature_id]
    
    def get(self, feature_id: str) -> Optional[FeatureConfig]:
        """Get a specific feature"""
        return self._features.get(feature_id)
    
    def get_all(self, status: Optional[FeatureStatus] = None, 
                category: Optional[FeatureCategory] = None) -> List[FeatureConfig]:
        """Get all features with optional filtering"""
        features = list(self._features.values())
        
        if status:
            features = [f for f in features if f.status == status]
        
        if category:
            features = [f for f in features if f.category == category]
        
        # Sort by order
        return sorted(features, key=lambda x: x.order)
    
    def get_active_features(self) -> List[FeatureConfig]:
        """Get all active features"""
        return self.get_all(status=FeatureStatus.ACTIVE)
    
    def get_features_for_user(self, user_permissions: List[str], 
                              department: Optional[str] = None) -> List[FeatureConfig]:
        """Get features accessible to a user based on permissions and department"""
        all_features = self.get_active_features()
        accessible = []
        
        for feature in all_features:
            # Check permissions
            if feature.required_permissions:
                if not all(perm in user_permissions for perm in feature.required_permissions):
                    continue
            
            # Check department access
            if feature.department_access and department:
                if department not in feature.department_access:
                    continue
            
            accessible.append(feature)
        
        return accessible
    
    def search(self, query: str) -> List[FeatureConfig]:
        """Search features by keywords"""
        query = query.lower()
        results = []
        
        for feature in self.get_active_features():
            # Search in name, description, keywords
            searchable = [
                feature.name.lower(),
                feature.description.lower(),
                *[k.lower() for k in feature.keywords]
            ]
            
            if any(query in text for text in searchable):
                results.append(feature)
        
        return results
    
    def get_by_category(self, category: FeatureCategory) -> List[FeatureConfig]:
        """Get features by category"""
        return self.get_all(category=category)
    
    def to_dict_list(self, features: Optional[List[FeatureConfig]] = None) -> List[Dict]:
        """Convert features to dict list for API"""
        if features is None:
            features = self.get_active_features()
        return [f.to_dict() for f in features]


# Global registry instance
feature_registry = FeatureRegistry()


def get_registry() -> FeatureRegistry:
    """Get the global feature registry instance"""
    return feature_registry
