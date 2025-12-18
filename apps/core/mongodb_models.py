"""
MongoDB Document Models for AIFlow
Using Python dataclasses for type safety and validation
"""

from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum


class IssueStatus(str, Enum):
    """Issue status enumeration"""
    PENDING = 'pending'
    APPROVED = 'approved'
    IGNORED = 'ignored'


class IssueSeverity(str, Enum):
    """Issue severity enumeration"""
    CRITICAL = 'critical'
    MAJOR = 'major'
    MINOR = 'minor'
    OBSERVATION = 'observation'


class AnalysisStatus(str, Enum):
    """Analysis status enumeration"""
    UPLOADED = 'uploaded'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'


class EmbeddingStatus(str, Enum):
    """Embedding status for RAG documents"""
    PENDING = 'pending'
    PROCESSING = 'processing'
    COMPLETED = 'completed'
    FAILED = 'failed'


@dataclass
class PIDIssueDocument:
    """MongoDB document for individual P&ID issues"""
    
    # Identifiers
    report_id: str
    serial_number: int
    
    # Issue details
    pid_reference: str
    issue_observed: str
    action_required: str
    
    # Classification
    severity: str = IssueSeverity.OBSERVATION.value
    category: str = ''
    
    # Review status
    status: str = IssueStatus.PENDING.value
    approval: str = 'Pending'
    remark: str = 'Pending'
    
    # Location information (for better analysis)
    location_x: Optional[float] = None
    location_y: Optional[float] = None
    page_number: Optional[int] = None
    
    # Timestamps
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    # MongoDB internal ID (set by MongoDB)
    _id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for MongoDB"""
        data = asdict(self)
        # Remove None _id if not set
        if data.get('_id') is None:
            data.pop('_id', None)
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PIDIssueDocument':
        """Create instance from dictionary"""
        # Convert string _id to str if present
        if '_id' in data:
            data['_id'] = str(data['_id'])
        
        # Convert datetime strings to datetime objects
        for field_name in ['created_at', 'updated_at']:
            if field_name in data and isinstance(data[field_name], str):
                data[field_name] = datetime.fromisoformat(data[field_name].replace('Z', '+00:00'))
        
        return cls(**data)


@dataclass
class PIDAnalysisReportDocument:
    """MongoDB document for P&ID analysis reports"""
    
    # Drawing reference (PostgreSQL foreign key)
    drawing_id: int
    user_id: int
    
    # Drawing metadata
    drawing_number: str = ''
    drawing_title: str = ''
    revision: str = ''
    project_name: str = ''
    original_filename: str = ''
    
    # Report summary
    total_issues: int = 0
    approved_count: int = 0
    ignored_count: int = 0
    pending_count: int = 0
    
    # Severity breakdown
    critical_count: int = 0
    major_count: int = 0
    minor_count: int = 0
    observation_count: int = 0
    
    # Full analysis data (unstructured)
    report_data: Dict[str, Any] = field(default_factory=dict)
    
    # Analysis metadata
    analysis_duration: Optional[float] = None  # seconds
    model_used: str = 'gpt-4'
    rag_enabled: bool = False
    reference_docs_used: List[str] = field(default_factory=list)
    
    # File references (S3 or local)
    pdf_report_url: Optional[str] = None
    excel_report_url: Optional[str] = None
    drawing_file_url: Optional[str] = None
    
    # Status tracking
    status: str = AnalysisStatus.COMPLETED.value
    error_message: Optional[str] = None
    
    # Timestamps
    generated_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    # MongoDB internal ID
    _id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for MongoDB"""
        data = asdict(self)
        if data.get('_id') is None:
            data.pop('_id', None)
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'PIDAnalysisReportDocument':
        """Create instance from dictionary"""
        if '_id' in data:
            data['_id'] = str(data['_id'])
        
        for field_name in ['generated_at', 'updated_at']:
            if field_name in data and isinstance(data[field_name], str):
                data[field_name] = datetime.fromisoformat(data[field_name].replace('Z', '+00:00'))
        
        return cls(**data)


@dataclass
class ReferenceDocumentDocument:
    """MongoDB document for reference documents with RAG data"""
    
    # Document metadata
    title: str
    description: str = ''
    category: str = 'other'
    
    # File information
    file_url: str = ''
    original_filename: str = ''
    file_size: int = 0
    
    # Content (for RAG)
    content_text: str = ''
    chunk_count: int = 0
    
    # Vector embeddings metadata
    vector_db_ids: List[str] = field(default_factory=list)
    embedding_status: str = EmbeddingStatus.PENDING.value
    embedding_model: str = 'text-embedding-ada-002'
    
    # Document details
    author: str = ''
    version: str = ''
    published_date: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    
    # User tracking
    uploaded_by_id: int = 0
    
    # Status
    is_active: bool = True
    
    # Timestamps
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    
    # MongoDB internal ID
    _id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for MongoDB"""
        data = asdict(self)
        if data.get('_id') is None:
            data.pop('_id', None)
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ReferenceDocumentDocument':
        """Create instance from dictionary"""
        if '_id' in data:
            data['_id'] = str(data['_id'])
        
        for field_name in ['created_at', 'updated_at']:
            if field_name in data and isinstance(data[field_name], str):
                data[field_name] = datetime.fromisoformat(data[field_name].replace('Z', '+00:00'))
        
        return cls(**data)


@dataclass
class AnalysisLogDocument:
    """MongoDB document for analysis execution logs"""
    
    drawing_id: int
    user_id: int
    
    # Log details
    action: str  # 'analysis_started', 'analysis_completed', 'analysis_failed', etc.
    message: str = ''
    
    # Context data
    context: Dict[str, Any] = field(default_factory=dict)
    
    # Performance metrics
    duration_ms: Optional[int] = None
    tokens_used: Optional[int] = None
    
    # Error tracking
    error_type: Optional[str] = None
    error_traceback: Optional[str] = None
    
    # Timestamp
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    # MongoDB internal ID
    _id: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for MongoDB"""
        data = asdict(self)
        if data.get('_id') is None:
            data.pop('_id', None)
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AnalysisLogDocument':
        """Create instance from dictionary"""
        if '_id' in data:
            data['_id'] = str(data['_id'])
        
        if 'timestamp' in data and isinstance(data['timestamp'], str):
            data['timestamp'] = datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
        
        return cls(**data)
