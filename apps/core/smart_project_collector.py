# Smart Project Document Collection System
# Automatically organize multi-disciplinary engineering documents by project

import os
import re
import logging
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from datetime import datetime
import json
from pathlib import Path

# Django imports
from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

# AI/ML imports for document analysis
try:
    import openai
    from sentence_transformers import SentenceTransformer
    import PyPDF2
    HAS_AI_LIBS = True
except ImportError:
    HAS_AI_LIBS = False

# RADAI imports
from apps.core.unified_s3_service import get_unified_s3_service
from apps.core.unified_folder_config import UnifiedFolderConfig

logger = logging.getLogger(__name__)

@dataclass
class ProjectDocument:
    """Enhanced document metadata with project and discipline classification"""
    document_id: str
    filename: str
    original_s3_key: str
    organized_s3_key: str
    project_code: str
    discipline: str  # mechanical, electrical, instrumentation, process, civil, etc.
    document_type: str  # pid, pfd, datasheet, specification, etc.
    document_subtype: Optional[str] = None  # pump_datasheet, valve_spec, etc.
    user_id: int = None
    file_size: int = 0
    upload_date: datetime = None
    extracted_metadata: Dict = None
    confidence_score: float = 0.0  # Confidence in automatic classification

@dataclass
class ProjectStructure:
    """Define project folder structure by discipline"""
    project_code: str
    project_name: str
    disciplines: Dict[str, List[str]]  # discipline -> list of document types
    created_at: datetime
    last_updated: datetime
    document_count: int = 0
    total_size_mb: float = 0.0

class SmartProjectCollector:
    """
    Intelligent document collection system that automatically organizes
    engineering documents by project and discipline
    """
    
    # Engineering disciplines and their common document types
    DISCIPLINE_PATTERNS = {
        'process': {
            'keywords': ['process', 'pfd', 'p&id', 'pid', 'flow', 'heat', 'mass', 'balance'],
            'document_types': ['pfd', 'pid_drawing', 'process_flow', 'heat_balance', 'mass_balance', 'utility_summary'],
            'file_patterns': [r'.*pfd.*', r'.*p&?id.*', r'.*process.*', r'.*flow.*']
        },
        'mechanical': {
            'keywords': ['mechanical', 'pump', 'compressor', 'turbine', 'vessel', 'tank', 'rotating', 'static'],
            'document_types': ['pump_datasheet', 'compressor_spec', 'vessel_datasheet', 'tank_specification', 'mechanical_drawing'],
            'file_patterns': [r'.*pump.*', r'.*compressor.*', r'.*vessel.*', r'.*tank.*', r'.*mechanical.*']
        },
        'electrical': {
            'keywords': ['electrical', 'motor', 'panel', 'switchgear', 'cable', 'power', 'lighting', 'grounding'],
            'document_types': ['motor_specification', 'panel_schedule', 'cable_schedule', 'electrical_drawing', 'power_study'],
            'file_patterns': [r'.*electrical.*', r'.*motor.*', r'.*panel.*', r'.*power.*', r'.*cable.*']
        },
        'instrumentation': {
            'keywords': ['instrument', 'control', 'automation', 'scada', 'dcs', 'plc', 'transmitter', 'valve'],
            'document_types': ['instrument_datasheet', 'control_valve_spec', 'transmitter_spec', 'loop_diagram', 'logic_diagram'],
            'file_patterns': [r'.*instrument.*', r'.*control.*', r'.*automation.*', r'.*loop.*', r'.*logic.*']
        },
        'civil': {
            'keywords': ['civil', 'structural', 'foundation', 'concrete', 'steel', 'building', 'layout'],
            'document_types': ['structural_drawing', 'foundation_plan', 'site_layout', 'civil_specification'],
            'file_patterns': [r'.*civil.*', r'.*structural.*', r'.*foundation.*', r'.*layout.*']
        },
        'piping': {
            'keywords': ['piping', 'pipeline', 'line', 'isometric', 'support', 'stress', 'routing'],
            'document_types': ['piping_iso', 'line_list', 'piping_plan', 'pipe_specification', 'support_drawing'],
            'file_patterns': [r'.*piping.*', r'.*iso.*', r'.*line.*', r'.*pipe.*']
        },
        'safety': {
            'keywords': ['safety', 'hazop', 'sil', 'fire', 'gas', 'emergency', 'shutdown', 'alarm'],
            'document_types': ['hazop_study', 'sil_assessment', 'fire_protection', 'gas_detection', 'safety_system'],
            'file_patterns': [r'.*safety.*', r'.*hazop.*', r'.*fire.*', r'.*emergency.*']
        }
    }
    
    # Common project code patterns
    PROJECT_CODE_PATTERNS = [
        r'([A-Z]{2,6}[-_]?[A-Z0-9]{4,8})',  # ADNOC-P16093, SABIC_P123
        r'([A-Z]{3,5}[-_]?\d{4,6})',        # ABC-1234, XYZ_123456
        r'(P[-_]?\d{4,6})',                 # P-16093, P_123456
        r'([A-Z]{2,4}\d{2,4}[A-Z]?\d{0,4})', # AB12C345
    ]
    
    def __init__(self):
        self.s3_service = get_unified_s3_service()
        self.folder_config = UnifiedFolderConfig()
        
        # Initialize AI models if available
        self.embedding_model = None
        self.openai_client = None
        
        if HAS_AI_LIBS:
            self._initialize_ai_models()
    
    def _initialize_ai_models(self):
        """Initialize AI models for document analysis"""
        try:
            model_name = getattr(settings, 'RADAI_EMBEDDING_MODEL', 'all-MiniLM-L6-v2')
            self.embedding_model = SentenceTransformer(model_name)
            
            openai_api_key = os.environ.get('OPENAI_API_KEY')
            if openai_api_key:
                self.openai_client = openai.OpenAI(api_key=openai_api_key)
            
            logger.info("AI models initialized for smart document collection")
            
        except Exception as e:
            logger.error(f"Failed to initialize AI models: {str(e)}")
    
    async def collect_and_organize_document(
        self,
        file_obj,
        filename: str,
        user_id: int,
        hint_project_code: Optional[str] = None,
        hint_discipline: Optional[str] = None,
        hint_document_type: Optional[str] = None
    ) -> ProjectDocument:
        """
        Smart document collection that automatically determines project,
        discipline, and optimal folder placement
        """
        try:
            # Step 1: Extract content for analysis
            content_text = await self._extract_document_content(file_obj, filename)
            
            # Step 2: Analyze document and extract project information
            analysis = await self._analyze_document_content(
                content_text, filename, hint_project_code, hint_discipline, hint_document_type
            )
            
            # Step 3: Determine optimal folder structure
            organized_s3_key = self._generate_organized_s3_key(
                analysis['project_code'],
                analysis['discipline'], 
                analysis['document_type'],
                analysis['document_subtype'],
                filename
            )
            
            # Step 4: Upload to organized location
            file_obj.seek(0)  # Reset file pointer
            upload_result = self.s3_service.upload_document(
                file_obj=file_obj,
                s3_key=organized_s3_key,
                metadata={
                    'project_code': analysis['project_code'],
                    'discipline': analysis['discipline'],
                    'document_type': analysis['document_type'],
                    'document_subtype': analysis['document_subtype'],
                    'user_id': str(user_id),
                    'auto_organized': 'true',
                    'confidence_score': str(analysis['confidence'])
                }
            )
            
            # Step 5: Create project document record
            project_doc = ProjectDocument(
                document_id=f"proj_{int(datetime.now().timestamp())}_{user_id}",
                filename=filename,
                original_s3_key=upload_result['s3_key'],
                organized_s3_key=organized_s3_key,
                project_code=analysis['project_code'],
                discipline=analysis['discipline'],
                document_type=analysis['document_type'],
                document_subtype=analysis['document_subtype'],
                user_id=user_id,
                file_size=len(file_obj.read()) if hasattr(file_obj, 'read') else 0,
                upload_date=timezone.now(),
                extracted_metadata=analysis['metadata'],
                confidence_score=analysis['confidence']
            )
            
            # Step 6: Update project structure
            await self._update_project_structure(project_doc)
            
            # Step 7: Trigger cross-discipline recommendations
            await self._trigger_cross_discipline_recommendations(project_doc)
            
            return project_doc
            
        except Exception as e:
            logger.error(f"Error in smart document collection: {str(e)}")
            raise
    
    async def _extract_document_content(self, file_obj, filename: str) -> str:
        """Extract text content from various document types"""
        try:
            file_extension = filename.lower().split('.')[-1]
            
            if file_extension == 'pdf':
                try:
                    file_obj.seek(0)
                    pdf_reader = PyPDF2.PdfReader(file_obj)
                    text_content = ""
                    
                    # Extract text from first 3 pages
                    for page_num in range(min(3, len(pdf_reader.pages))):
                        page = pdf_reader.pages[page_num]
                        text_content += page.extract_text() + "\n"
                    
                    return text_content
                except Exception as pdf_error:
                    logger.warning(f"PDF text extraction failed: {pdf_error}")
                    return filename  # Fallback to filename analysis
            
            elif file_extension in ['txt', 'csv']:
                try:
                    file_obj.seek(0)
                    content = file_obj.read()
                    if isinstance(content, bytes):
                        content = content.decode('utf-8', errors='ignore')
                    return content[:2000]  # First 2000 characters
                except Exception as text_error:
                    logger.warning(f"Text extraction failed: {text_error}")
                    return filename
            
            else:
                # For other file types, analyze filename and any metadata
                return filename
                
        except Exception as e:
            logger.error(f"Content extraction error: {str(e)}")
            return filename
    
    async def _analyze_document_content(
        self, 
        content: str, 
        filename: str,
        hint_project_code: Optional[str] = None,
        hint_discipline: Optional[str] = None,
        hint_document_type: Optional[str] = None
    ) -> Dict:
        """
        Analyze document content to determine project, discipline, and type
        """
        analysis = {
            'project_code': hint_project_code or 'UNKNOWN',
            'discipline': hint_discipline or 'general',
            'document_type': hint_document_type or 'document',
            'document_subtype': None,
            'confidence': 0.5,  # Default confidence
            'metadata': {}
        }
        
        try:
            # Extract project code
            if not hint_project_code:
                project_code = self._extract_project_code(content, filename)
                if project_code:
                    analysis['project_code'] = project_code
                    analysis['confidence'] += 0.2
            
            # Determine discipline
            if not hint_discipline:
                discipline_analysis = self._classify_discipline(content, filename)
                analysis['discipline'] = discipline_analysis['discipline']
                analysis['confidence'] = max(analysis['confidence'], discipline_analysis['confidence'])
            
            # Determine document type
            if not hint_document_type:
                doc_type_analysis = self._classify_document_type(
                    content, filename, analysis['discipline']
                )
                analysis['document_type'] = doc_type_analysis['document_type']
                analysis['document_subtype'] = doc_type_analysis['document_subtype']
                analysis['confidence'] = max(analysis['confidence'], doc_type_analysis['confidence'])
            
            # Use AI for enhanced analysis if available and content is substantial
            if self.openai_client and len(content) > 100:
                ai_analysis = await self._ai_enhanced_analysis(content, filename)
                if ai_analysis:
                    # Update analysis with AI insights
                    analysis['metadata'].update(ai_analysis)
                    analysis['confidence'] = min(analysis['confidence'] + 0.1, 1.0)
            
            return analysis
            
        except Exception as e:
            logger.error(f"Document analysis error: {str(e)}")
            return analysis
    
    def _extract_project_code(self, content: str, filename: str) -> Optional[str]:
        """Extract project code using pattern matching"""
        combined_text = f"{filename} {content}"
        
        for pattern in self.PROJECT_CODE_PATTERNS:
            matches = re.findall(pattern, combined_text, re.IGNORECASE)
            if matches:
                # Return the first, most likely project code
                project_code = matches[0].upper()
                # Clean up the project code
                project_code = re.sub(r'[_\s]+', '-', project_code)
                return project_code
        
        return None
    
    def _classify_discipline(self, content: str, filename: str) -> Dict:
        """Classify document discipline using keywords and patterns"""
        combined_text = f"{filename} {content}".lower()
        discipline_scores = {}
        
        # Score each discipline based on keyword matches
        for discipline, config in self.DISCIPLINE_PATTERNS.items():
            score = 0
            
            # Keyword scoring
            for keyword in config['keywords']:
                keyword_count = combined_text.count(keyword.lower())
                score += keyword_count * 2
            
            # Pattern scoring
            for pattern in config['file_patterns']:
                if re.search(pattern, filename, re.IGNORECASE):
                    score += 10
            
            if score > 0:
                discipline_scores[discipline] = score
        
        if discipline_scores:
            best_discipline = max(discipline_scores, key=discipline_scores.get)
            max_score = discipline_scores[best_discipline]
            confidence = min(max_score / 20.0, 1.0)  # Normalize to 0-1
            
            return {
                'discipline': best_discipline,
                'confidence': confidence
            }
        
        return {'discipline': 'general', 'confidence': 0.1}
    
    def _classify_document_type(self, content: str, filename: str, discipline: str) -> Dict:
        """Classify specific document type within a discipline"""
        combined_text = f"{filename} {content}".lower()
        
        # Get discipline-specific document types
        if discipline in self.DISCIPLINE_PATTERNS:
            doc_types = self.DISCIPLINE_PATTERNS[discipline]['document_types']
        else:
            doc_types = ['specification', 'drawing', 'datasheet', 'report']
        
        type_scores = {}
        
        # Score document types based on content
        for doc_type in doc_types:
            score = 0
            
            # Direct type mentions
            if doc_type.replace('_', ' ') in combined_text:
                score += 10
            
            # Common document type indicators
            type_keywords = {
                'datasheet': ['datasheet', 'data sheet', 'specification', 'spec'],
                'drawing': ['drawing', 'dwg', 'diagram', 'iso', 'plan'],
                'specification': ['specification', 'spec', 'requirement'],
                'report': ['report', 'study', 'analysis', 'assessment']
            }
            
            for base_type, keywords in type_keywords.items():
                if base_type in doc_type:
                    for keyword in keywords:
                        if keyword in combined_text:
                            score += 3
            
            if score > 0:
                type_scores[doc_type] = score
        
        if type_scores:
            best_type = max(type_scores, key=type_scores.get)
            confidence = min(type_scores[best_type] / 15.0, 1.0)
            
            # Determine subtype
            subtype = None
            if '_' in best_type:
                subtype = best_type.split('_')[0]  # e.g., 'pump' from 'pump_datasheet'
            
            return {
                'document_type': best_type,
                'document_subtype': subtype,
                'confidence': confidence
            }
        
        return {
            'document_type': 'document',
            'document_subtype': None,
            'confidence': 0.1
        }
    
    async def _ai_enhanced_analysis(self, content: str, filename: str) -> Optional[Dict]:
        """Use OpenAI for enhanced document analysis"""
        if not self.openai_client:
            return None
        
        try:
            # Truncate content for API efficiency
            truncated_content = content[:2000]
            
            prompt = f"""
            Analyze this engineering document and extract metadata as JSON:
            
            Filename: {filename}
            Content: {truncated_content}
            
            Extract:
            - project_identifiers: Any project codes or names
            - equipment_tags: Equipment tag numbers (P-101, V-203, etc.)
            - technical_specs: Key technical specifications
            - revision_info: Revision numbers or dates
            - discipline_indicators: Engineering discipline clues
            - document_purpose: Brief purpose description
            
            Return only valid JSON, no other text.
            """
            
            response = await self.openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400,
                temperature=0.1
            )
            
            return json.loads(response.choices[0].message.content)
            
        except Exception as e:
            logger.error(f"AI analysis error: {str(e)}")
            return None
    
    def _generate_organized_s3_key(
        self,
        project_code: str,
        discipline: str, 
        document_type: str,
        document_subtype: Optional[str],
        filename: str
    ) -> str:
        """Generate organized S3 key using unified folder configuration"""
        
        # Use unified folder configuration for consistent path structure
        try:
            # Get the base project document path using unified config
            s3_path = UnifiedFolderConfig.get_smart_document_path(
                filename=filename,
                project_code=project_code,
                discipline=discipline,
                document_type=document_type
            )
            
            # If document has subtype, insert it before filename
            if document_subtype:
                # Split path and filename
                path_parts = s3_path.rsplit('/', 1)
                if len(path_parts) == 2:
                    folder_path, file_name = path_parts
                    safe_subtype = re.sub(r'[^A-Za-z0-9\-_]', '', document_subtype)
                    s3_path = f"{folder_path}/{safe_subtype}/{file_name}"
            
            return s3_path
            
        except Exception as e:
            logger.warning(f"Failed to generate path using unified config: {str(e)}")
            
            # Fallback to legacy path generation
            safe_project = re.sub(r'[^A-Za-z0-9\-_]', '', project_code)
            safe_discipline = re.sub(r'[^A-Za-z0-9\-_]', '', discipline)
            safe_doc_type = re.sub(r'[^A-Za-z0-9\-_]', '', document_type)
            
            path_components = [
                'projects',
                safe_project,
                'disciplines', 
                safe_discipline,
                safe_doc_type
            ]
            
            if document_subtype:
                safe_subtype = re.sub(r'[^A-Za-z0-9\-_]', '', document_subtype)
                path_components.append(safe_subtype)
                
            path_components.append(filename)
            
            return '/'.join(path_components)
    
    async def _update_project_structure(self, project_doc: ProjectDocument):
        """Update project structure tracking"""
        cache_key = f"project_structure_{project_doc.project_code}"
        
        try:
            project_structure = cache.get(cache_key)
            
            if not project_structure:
                # Create new project structure
                project_structure = ProjectStructure(
                    project_code=project_doc.project_code,
                    project_name=project_doc.project_code,  # Could be enhanced with AI
                    disciplines={},
                    created_at=timezone.now(),
                    last_updated=timezone.now()
                )
            
            # Update discipline and document type tracking
            if project_doc.discipline not in project_structure.disciplines:
                project_structure.disciplines[project_doc.discipline] = []
            
            if project_doc.document_type not in project_structure.disciplines[project_doc.discipline]:
                project_structure.disciplines[project_doc.discipline].append(project_doc.document_type)
            
            # Update statistics
            project_structure.document_count += 1
            project_structure.total_size_mb += project_doc.file_size / (1024 * 1024)
            project_structure.last_updated = timezone.now()
            
            # Cache updated structure
            cache.set(cache_key, project_structure, timeout=86400)  # 24 hours
            
        except Exception as e:
            logger.error(f"Error updating project structure: {str(e)}")
    
    async def _trigger_cross_discipline_recommendations(self, project_doc: ProjectDocument):
        """Trigger recommendations across disciplines for the project"""
        try:
            # This integrates with the recommendation system
            from apps.recommendations.ai_recommendation_engine import get_recommendation_engine
            
            rec_engine = get_recommendation_engine()
            
            # Find related documents in other disciplines for the same project
            project_docs = await self._get_project_documents(
                project_doc.project_code, 
                exclude_discipline=project_doc.discipline
            )
            
            if project_docs:
                logger.info(f"Found {len(project_docs)} cross-discipline documents for {project_doc.project_code}")
                # This would trigger enhanced recommendations
                # based on cross-discipline document patterns
            
        except Exception as e:
            logger.error(f"Error triggering cross-discipline recommendations: {str(e)}")
    
    async def _get_project_documents(
        self, 
        project_code: str, 
        discipline: Optional[str] = None,
        exclude_discipline: Optional[str] = None
    ) -> List[ProjectDocument]:
        """Get all documents for a project, optionally filtered by discipline"""
        try:
            # This would query your document database
            # For now, return empty list as placeholder
            return []
            
        except Exception as e:
            logger.error(f"Error getting project documents: {str(e)}")
            return []
    
    def get_project_overview(self, project_code: str) -> Optional[Dict]:
        """Get comprehensive project document overview"""
        cache_key = f"project_structure_{project_code}"
        project_structure = cache.get(cache_key)
        
        if not project_structure:
            return None
        
        return {
            'project_code': project_structure.project_code,
            'project_name': project_structure.project_name,
            'total_documents': project_structure.document_count,
            'total_size_mb': round(project_structure.total_size_mb, 2),
            'disciplines': project_structure.disciplines,
            'last_updated': project_structure.last_updated.isoformat(),
            'folder_structure': self._generate_project_folder_preview(project_structure)
        }
    
    def _generate_project_folder_preview(self, project_structure: ProjectStructure) -> Dict:
        """Generate a preview of the project folder structure"""
        structure = {
            'root': f"projects/{project_structure.project_code}/",
            'disciplines': {}
        }
        
        for discipline, doc_types in project_structure.disciplines.items():
            structure['disciplines'][discipline] = {
                'path': f"projects/{project_structure.project_code}/disciplines/{discipline}/",
                'document_types': doc_types
            }
        
        return structure

# Singleton pattern
_smart_collector = None

def get_smart_project_collector() -> SmartProjectCollector:
    """Get the singleton smart project collector instance"""
    global _smart_collector
    if _smart_collector is None:
        _smart_collector = SmartProjectCollector()
    return _smart_collector