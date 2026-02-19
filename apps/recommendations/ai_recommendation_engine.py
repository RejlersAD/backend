# RADAI Intelligent Recommendation System
# Generative AI-powered document recommendations for engineering data

import os
import hashlib
import json
import numpy as np
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
import logging
from functools import lru_cache

# Django imports
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

# AI/ML imports
try:
    import openai
    from sentence_transformers import SentenceTransformer
    import torch
    from sklearn.metrics.pairwise import cosine_similarity
    from PIL import Image
    import PyPDF2
    import pandas as pd
    HAS_AI_LIBS = True
except ImportError:
    HAS_AI_LIBS = False

# RADAI imports
from apps.core.unified_s3_service import get_unified_s3_service
from apps.core.unified_folder_config import UnifiedFolderConfig

logger = logging.getLogger(__name__)

@dataclass
class DocumentMetadata:
    """Enhanced document metadata for AI analysis"""
    document_id: str
    s3_key: str
    filename: str
    document_type: str
    file_size: int
    upload_date: datetime
    user_id: int
    project_code: Optional[str] = None
    content_hash: Optional[str] = None
    semantic_embedding: Optional[List[float]] = None
    ai_extracted_metadata: Optional[Dict] = None
    similarity_score: Optional[float] = None

@dataclass 
class RecommendationResult:
    """AI recommendation result"""
    recommendation_type: str  # 'duplicate', 'similar', 'related', 'quality_check', 'auto_complete'
    confidence_score: float
    recommended_documents: List[DocumentMetadata]
    ai_reasoning: str
    suggested_actions: List[str]
    metadata: Dict[str, Any]

class RadaiRecommendationEngine:
    """
    Generative AI-powered recommendation engine for RADAI engineering documents
    
    Features:
    - Document similarity detection using embeddings
    - Duplicate/near-duplicate identification  
    - Content-based recommendations
    - Quality assessment suggestions
    - Auto-completion recommendations
    - Cross-user learning capabilities
    """
    
    def __init__(self):
        self.s3_service = get_unified_s3_service()
        self.folder_config = UnifiedFolderConfig()
        
        # Initialize AI models if available
        self.embedding_model = None
        self.openai_client = None
        
        if HAS_AI_LIBS:
            self._initialize_ai_models()
        else:
            logger.warning("AI libraries not available. Using fallback methods.")
    
    def _initialize_ai_models(self):
        """Initialize AI models for document analysis"""
        try:
            # Initialize sentence transformer for document embeddings
            model_name = getattr(settings, 'RADAI_EMBEDDING_MODEL', 'all-MiniLM-L6-v2')
            self.embedding_model = SentenceTransformer(model_name)
            
            # Initialize OpenAI client for advanced analysis
            openai_api_key = os.environ.get('OPENAI_API_KEY')
            if openai_api_key:
                self.openai_client = openai.OpenAI(api_key=openai_api_key)
            
            logger.info("AI models initialized successfully")
            
        except Exception as e:
            logger.error(f"Failed to initialize AI models: {str(e)}")
            self.embedding_model = None
            self.openai_client = None
    
    async def analyze_upload(
        self, 
        file_obj, 
        filename: str,
        document_type: str, 
        user_id: int,
        project_code: Optional[str] = None
    ) -> List[RecommendationResult]:
        """
        Analyze uploaded document and generate AI recommendations
        
        Args:
            file_obj: File object to analyze
            filename: Name of the file
            document_type: Type of document (PID, PFD, specification, etc.)
            user_id: ID of uploading user
            project_code: Optional project code
            
        Returns:
            List of recommendation results with AI insights
        """
        recommendations = []
        
        try:
            # Extract document content and metadata
            doc_metadata = await self._extract_document_metadata(
                file_obj, filename, document_type, user_id, project_code
            )
            
            # Run parallel analysis for different recommendation types
            duplicate_recs = await self._find_duplicates(doc_metadata)
            if duplicate_recs:
                recommendations.extend(duplicate_recs)
            
            similarity_recs = await self._find_similar_documents(doc_metadata)
            if similarity_recs:
                recommendations.extend(similarity_recs)
            
            quality_recs = await self._assess_document_quality(doc_metadata)
            if quality_recs:
                recommendations.extend(quality_recs)
            
            completion_recs = await self._suggest_complementary_documents(doc_metadata)
            if completion_recs:
                recommendations.extend(completion_recs)
            
            # Cache results for performance
            cache_key = f"radai_recommendations_{doc_metadata.content_hash}"
            cache.set(cache_key, recommendations, timeout=3600)  # 1 hour
            
            return recommendations
            
        except Exception as e:
            logger.error(f"Error analyzing upload: {str(e)}")
            return []
    
    async def _extract_document_metadata(
        self, 
        file_obj, 
        filename: str,
        document_type: str,
        user_id: int,
        project_code: Optional[str]
    ) -> DocumentMetadata:
        """Extract comprehensive metadata from document"""
        
        # Calculate content hash for duplicate detection
        file_obj.seek(0)
        content = file_obj.read()
        content_hash = hashlib.sha256(content).hexdigest()
        file_obj.seek(0)
        
        # Generate semantic embedding
        semantic_embedding = None
        ai_metadata = {}
        
        if self.embedding_model:
            try:
                # Extract text content based on file type
                text_content = await self._extract_text_content(file_obj, filename)
                
                if text_content:
                    # Generate embedding
                    semantic_embedding = self.embedding_model.encode(text_content).tolist()
                    
                    # Extract AI metadata using LLM
                    if self.openai_client and len(text_content) > 50:
                        ai_metadata = await self._extract_ai_metadata(text_content, document_type)
                
            except Exception as e:
                logger.error(f"Error extracting semantic data: {str(e)}")
        
        return DocumentMetadata(
            document_id=f"doc_{int(datetime.now().timestamp())}_{user_id}",
            s3_key="",  # Will be set after upload
            filename=filename,
            document_type=document_type,
            file_size=len(content),
            upload_date=timezone.now(),
            user_id=user_id,
            project_code=project_code,
            content_hash=content_hash,
            semantic_embedding=semantic_embedding,
            ai_extracted_metadata=ai_metadata
        )
    
    async def _extract_text_content(self, file_obj, filename: str) -> str:
        """Extract text content from various file types"""
        try:
            file_extension = filename.lower().split('.')[-1]
            
            if file_extension == 'pdf':
                return await self._extract_pdf_text(file_obj)
            elif file_extension in ['txt', 'csv', 'json']:
                file_obj.seek(0)
                return file_obj.read().decode('utf-8', errors='ignore')
            elif file_extension in ['png', 'jpg', 'jpeg', 'tiff']:
                # For images, use filename and any OCR if available
                return f"Image document: {filename}"
            else:
                return f"Document: {filename}"
                
        except Exception as e:
            logger.error(f"Error extracting text content: {str(e)}")
            return f"Document: {filename}"
    
    async def _extract_pdf_text(self, file_obj) -> str:
        """Extract text from PDF files"""
        try:
            file_obj.seek(0)
            pdf_reader = PyPDF2.PdfReader(file_obj)
            text_content = ""
            
            for page in pdf_reader.pages[:5]:  # First 5 pages only
                text_content += page.extract_text() + "\n"
            
            return text_content.strip()
            
        except Exception as e:
            logger.error(f"Error extracting PDF text: {str(e)}")
            return ""
    
    async def _extract_ai_metadata(self, text_content: str, document_type: str) -> Dict:
        """Use OpenAI to extract structured metadata"""
        if not self.openai_client:
            return {}
        
        try:
            # Truncate content for API efficiency
            truncated_content = text_content[:4000]
            
            prompt = f"""
            Analyze this {document_type} document and extract key metadata as JSON:
            
            Document content:
            {truncated_content}
            
            Please extract and return JSON with these fields:
            - equipment_types: List of equipment mentioned
            - specifications: Key technical specifications
            - project_identifiers: Any project codes or identifiers
            - document_purpose: Brief description of document purpose
            - quality_indicators: Any quality or standard references
            - related_systems: Related engineering systems mentioned
            
            Return only valid JSON, no other text.
            """
            
            response = await self.openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.1
            )
            
            return json.loads(response.choices[0].message.content)
            
        except Exception as e:
            logger.error(f"Error extracting AI metadata: {str(e)}")
            return {}
    
    async def _find_duplicates(self, doc_metadata: DocumentMetadata) -> List[RecommendationResult]:
        """Find exact or near-duplicate documents"""
        recommendations = []
        
        try:
            # Check for exact content hash matches
            cache_key = f"radai_content_hashes"
            existing_hashes = cache.get(cache_key, {})
            
            if doc_metadata.content_hash in existing_hashes:
                duplicate_docs = existing_hashes[doc_metadata.content_hash]
                
                recommendations.append(RecommendationResult(
                    recommendation_type="duplicate",
                    confidence_score=1.0,
                    recommended_documents=duplicate_docs,
                    ai_reasoning=f"Exact duplicate detected based on content hash. This document is identical to {len(duplicate_docs)} existing document(s).",
                    suggested_actions=[
                        "Review existing document before uploading",
                        "Consider if this is a version update",
                        "Check if upload is necessary"
                    ],
                    metadata={
                        "duplicate_type": "exact",
                        "content_hash": doc_metadata.content_hash
                    }
                ))
            
            # Check for semantic near-duplicates if embeddings available
            if doc_metadata.semantic_embedding and self.embedding_model:
                near_duplicates = await self._find_semantic_duplicates(doc_metadata)
                if near_duplicates:
                    recommendations.extend(near_duplicates)
            
            return recommendations
            
        except Exception as e:
            logger.error(f"Error finding duplicates: {str(e)}")
            return []
    
    async def _find_semantic_duplicates(self, doc_metadata: DocumentMetadata) -> List[RecommendationResult]:
        """Find semantically similar documents that might be duplicates"""
        try:
            # This would integrate with your document database
            # For now, showing the structure
            
            # Get existing document embeddings from database/cache
            cache_key = f"radai_document_embeddings_{doc_metadata.document_type}"
            existing_embeddings = cache.get(cache_key, [])
            
            if not existing_embeddings:
                return []
            
            # Calculate cosine similarity
            similarities = []
            doc_embedding = np.array(doc_metadata.semantic_embedding).reshape(1, -1)
            
            for existing_doc, embedding in existing_embeddings:
                existing_embedding = np.array(embedding).reshape(1, -1)
                similarity = cosine_similarity(doc_embedding, existing_embedding)[0][0]
                
                if similarity > 0.95:  # Very high similarity threshold
                    similarities.append((existing_doc, similarity))
            
            if similarities:
                # Sort by similarity
                similarities.sort(key=lambda x: x[1], reverse=True)
                top_similar = similarities[:3]
                
                return [RecommendationResult(
                    recommendation_type="duplicate",
                    confidence_score=top_similar[0][1],
                    recommended_documents=[doc for doc, _ in top_similar],
                    ai_reasoning=f"Detected {len(top_similar)} semantically similar documents with {top_similar[0][1]:.2%} similarity. These may be near-duplicates or different versions of the same document.",
                    suggested_actions=[
                        "Compare document contents",
                        "Check creation dates",
                        "Verify this is a new version"
                    ],
                    metadata={
                        "duplicate_type": "semantic",
                        "similarity_scores": [score for _, score in top_similar]
                    }
                )]
            
            return []
            
        except Exception as e:
            logger.error(f"Error finding semantic duplicates: {str(e)}")
            return []
    
    async def _find_similar_documents(self, doc_metadata: DocumentMetadata) -> List[RecommendationResult]:
        """Find similar documents that might be useful references"""
        recommendations = []
        
        try:
            if not doc_metadata.semantic_embedding:
                return []
            
            # Find documents with moderate similarity (not duplicates but related)
            cache_key = f"radai_similar_docs_{doc_metadata.document_type}"
            similar_docs = await self._get_similar_by_embedding(
                doc_metadata.semantic_embedding,
                doc_metadata.document_type,
                similarity_threshold=(0.7, 0.94)  # Not too low, not duplicate level
            )
            
            if similar_docs:
                recommendations.append(RecommendationResult(
                    recommendation_type="similar",
                    confidence_score=0.85,
                    recommended_documents=similar_docs[:5],  # Top 5
                    ai_reasoning=f"Found {len(similar_docs)} similar {doc_metadata.document_type} documents that other users have uploaded. These might provide useful references or templates.",
                    suggested_actions=[
                        "Review similar documents for reference",
                        "Check for standard templates",
                        "Compare technical specifications"
                    ],
                    metadata={
                        "similarity_type": "content_based",
                        "document_type": doc_metadata.document_type
                    }
                ))
            
            return recommendations
            
        except Exception as e:
            logger.error(f"Error finding similar documents: {str(e)}")
            return []
    
    async def _assess_document_quality(self, doc_metadata: DocumentMetadata) -> List[RecommendationResult]:
        """Assess document quality and suggest improvements"""
        recommendations = []
        
        try:
            quality_issues = []
            quality_suggestions = []
            
            # File size analysis
            if doc_metadata.file_size > 50 * 1024 * 1024:  # 50MB
                quality_issues.append("Large file size")
                quality_suggestions.append("Consider compressing the document for better performance")
            
            # Filename analysis
            if not any(keyword in doc_metadata.filename.lower() for keyword in ['pid', 'pfd', 'spec', 'datasheet']):
                quality_issues.append("Non-descriptive filename")
                quality_suggestions.append("Consider using descriptive filenames with document type")
            
            # AI-based quality assessment
            if doc_metadata.ai_extracted_metadata and self.openai_client:
                ai_quality = await self._ai_quality_assessment(doc_metadata)
                quality_issues.extend(ai_quality.get('issues', []))
                quality_suggestions.extend(ai_quality.get('suggestions', []))
            
            if quality_issues:
                recommendations.append(RecommendationResult(
                    recommendation_type="quality_check",
                    confidence_score=0.8,
                    recommended_documents=[],
                    ai_reasoning=f"Identified {len(quality_issues)} potential quality improvements for this document.",
                    suggested_actions=quality_suggestions,
                    metadata={
                        "quality_issues": quality_issues,
                        "assessment_type": "automated"
                    }
                ))
            
            return recommendations
            
        except Exception as e:
            logger.error(f"Error assessing document quality: {str(e)}")
            return []
    
    async def _suggest_complementary_documents(self, doc_metadata: DocumentMetadata) -> List[RecommendationResult]:
        """Suggest complementary documents based on upload patterns"""
        recommendations = []
        
        try:
            # Analyze what other users typically upload together
            complementary_types = await self._get_complementary_document_types(
                doc_metadata.document_type,
                doc_metadata.project_code
            )
            
            if complementary_types:
                recommendations.append(RecommendationResult(
                    recommendation_type="auto_complete",
                    confidence_score=0.75,
                    recommended_documents=[],
                    ai_reasoning=f"Based on upload patterns, users who upload {doc_metadata.document_type} documents typically also need {', '.join(complementary_types)}.",
                    suggested_actions=[
                        f"Consider uploading {doc_type} documents" for doc_type in complementary_types[:3]
                    ],
                    metadata={
                        "complementary_types": complementary_types,
                        "pattern_based": True
                    }
                ))
            
            return recommendations
            
        except Exception as e:
            logger.error(f"Error suggesting complementary documents: {str(e)}")
            return []
    
    async def _ai_quality_assessment(self, doc_metadata: DocumentMetadata) -> Dict:
        """Use AI to assess document quality"""
        if not self.openai_client or not doc_metadata.ai_extracted_metadata:
            return {}
        
        try:
            metadata_str = json.dumps(doc_metadata.ai_extracted_metadata, indent=2)
            
            prompt = f"""
            Assess the quality of this {doc_metadata.document_type} document based on its extracted metadata:
            
            {metadata_str}
            
            Return JSON with:
            - issues: List of potential quality issues
            - suggestions: List of improvement suggestions
            - quality_score: Overall quality score (0-10)
            """
            
            response = await self.openai_client.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.1
            )
            
            return json.loads(response.choices[0].message.content)
            
        except Exception as e:
            logger.error(f"Error in AI quality assessment: {str(e)}")
            return {}
    
    async def _get_similar_by_embedding(
        self, 
        embedding: List[float], 
        document_type: str,
        similarity_threshold: Tuple[float, float] = (0.7, 0.95)
    ) -> List[DocumentMetadata]:
        """Get similar documents by embedding similarity"""
        # This would integrate with your database
        # Return documents within similarity threshold range
        return []
    
    async def _get_complementary_document_types(
        self, 
        document_type: str, 
        project_code: Optional[str]
    ) -> List[str]:
        """Get document types commonly uploaded together"""
        # Common patterns in engineering documentation
        complementary_patterns = {
            'pid_drawing': ['pump_datasheet', 'valve_specification', 'instrument_list'],
            'pfd_document': ['pid_drawing', 'equipment_list', 'utility_summary'],
            'pump_datasheet': ['motor_specification', 'pid_drawing', 'installation_drawing'],
            'valve_specification': ['pid_drawing', 'actuator_datasheet'],
            'instrument_specification': ['pid_drawing', 'loop_diagram', 'installation_detail']
        }
        
        return complementary_patterns.get(document_type, [])
    
    @lru_cache(maxsize=100)
    def get_user_upload_patterns(self, user_id: int) -> Dict:
        """Analyze user's historical upload patterns"""
        # This would analyze user's upload history
        # Cache results for performance
        return {}
    
    def get_recommendation_statistics(self) -> Dict:
        """Get recommendation engine statistics"""
        return {
            'total_recommendations_generated': cache.get('radai_total_recommendations', 0),
            'duplicate_detections': cache.get('radai_duplicate_detections', 0),
            'quality_improvements': cache.get('radai_quality_improvements', 0),
            'ai_models_active': bool(self.embedding_model and self.openai_client),
            'cache_hit_rate': cache.get('radai_cache_hit_rate', 0.0)
        }

# Singleton pattern
_recommendation_engine = None

def get_recommendation_engine() -> RadaiRecommendationEngine:
    """Get the singleton recommendation engine instance"""
    global _recommendation_engine
    if _recommendation_engine is None:
        _recommendation_engine = RadaiRecommendationEngine()
    return _recommendation_engine