"""
Simplified RAG Service using OpenAI Embeddings
Stores vectors in PostgreSQL for persistence
"""
import os
import json
from typing import List, Dict, Any, Optional
from openai import OpenAI
import numpy as np


class RAGService:
    """
    Simplified RAG service using OpenAI embeddings
    Stores document chunks and embeddings in the ReferenceDocument model
    """
    
    def __init__(self):
        """Initialize RAG service with OpenAI client"""
        api_key = os.environ.get('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable is required")
        
        self.client = OpenAI(api_key=api_key)
        self.embedding_model = os.environ.get('EMBEDDING_MODEL', 'text-embedding-3-small')
    
    def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for text using OpenAI
        
        Args:
            text: Text to embed
        
        Returns:
            List of floats representing the embedding vector
        """
        try:
            response = self.client.embeddings.create(
                model=self.embedding_model,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            print(f"[ERROR] Failed to generate embedding: {str(e)}")
            raise
    
    def add_reference_document(
        self,
        document_id: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> List[str]:
        """
        Add reference document to RAG system
        
        Args:
            document_id: Unique document identifier
            content: Document text content
            metadata: Additional metadata
        
        Returns:
            List of chunk IDs that were added
        """
        from .document_processor import DocumentProcessor
        
        # Chunk the document
        processor = DocumentProcessor()
        chunks = processor.chunk_text(content, metadata=metadata)
        
        print(f"[INFO] Processing {len(chunks)} chunks for document {document_id}")
        
        # Generate embeddings for each chunk
        chunk_data = []
        
        for i, chunk_dict in enumerate(chunks):
            chunk_id = f"{document_id}_chunk_{i}"
            chunk_text = chunk_dict['text']
            
            try:
                # Generate embedding
                embedding = self.generate_embedding(chunk_text)
                
                # Store chunk data
                chunk_data.append({
                    'id': chunk_id,
                    'text': chunk_text,
                    'embedding': embedding,
                    'metadata': {
                        **(metadata or {}),
                        **chunk_dict.get('metadata', {})
                    }
                })
                
            except Exception as e:
                print(f"[WARNING] Failed to embed chunk {i}: {str(e)}")
                continue
        
        # Store chunk data in the document's vector_db_ids field as JSON
        # This will be retrieved later for similarity search
        return chunk_data
    
    def retrieve_context(
        self,
        query: str,
        top_k: Optional[int] = None,
        similarity_threshold: Optional[float] = None
    ) -> str:
        """
        Retrieve relevant context for a query
        
        Args:
            query: Search query
            top_k: Number of top results to return
            similarity_threshold: Minimum similarity score
        
        Returns:
            Concatenated context text from relevant chunks
        """
        from .models import ReferenceDocument
        
        # Get configuration
        top_k = top_k or int(os.environ.get('RAG_TOP_K', '5'))
        similarity_threshold = similarity_threshold or float(os.environ.get('RAG_SIMILARITY_THRESHOLD', '0.7'))
        
        try:
            # Generate query embedding
            query_embedding = self.generate_embedding(query)
            
            # Get all active reference documents
            documents = ReferenceDocument.objects.filter(
                is_active=True,
                embedding_status='completed'
            )
            
            if not documents.exists():
                print("[INFO] No active reference documents found")
                return ""
            
            # Calculate similarities
            all_chunks = []
            
            for doc in documents:
                if not doc.vector_db_ids:
                    continue
                
                try:
                    # Parse stored chunk data
                    if isinstance(doc.vector_db_ids, str):
                        chunk_data = json.loads(doc.vector_db_ids)
                    else:
                        chunk_data = doc.vector_db_ids
                    
                    for chunk in chunk_data:
                        if 'embedding' in chunk and 'text' in chunk:
                            # Calculate cosine similarity
                            similarity = self._cosine_similarity(
                                query_embedding,
                                chunk['embedding']
                            )
                            
                            if similarity >= similarity_threshold:
                                all_chunks.append({
                                    'text': chunk['text'],
                                    'similarity': similarity,
                                    'metadata': chunk.get('metadata', {})
                                })
                
                except Exception as e:
                    print(f"[WARNING] Failed to process document {doc.id}: {str(e)}")
                    continue
            
            if not all_chunks:
                print(f"[INFO] No relevant chunks found (threshold: {similarity_threshold})")
                return ""
            
            # Sort by similarity and take top_k
            all_chunks.sort(key=lambda x: x['similarity'], reverse=True)
            top_chunks = all_chunks[:top_k]
            
            # Build context
            context_parts = []
            for chunk in top_chunks:
                metadata = chunk.get('metadata', {})
                title = metadata.get('title', 'Unknown')
                category = metadata.get('category', 'document')
                
                context_parts.append(
                    f"[{category.upper()}: {title}]\n{chunk['text']}\n"
                )
            
            context = "\n---\n".join(context_parts)
            
            print(f"[INFO] Retrieved {len(top_chunks)} relevant chunks")
            return context
            
        except Exception as e:
            print(f"[ERROR] Context retrieval failed: {str(e)}")
            import traceback
            traceback.print_exc()
            return ""
    
    def build_augmented_prompt(self, base_prompt: str, query: str) -> str:
        """
        Build augmented prompt with retrieved context
        
        Args:
            base_prompt: Original prompt
            query: Query to retrieve context for
        
        Returns:
            Augmented prompt with context
        """
        context = self.retrieve_context(query)
        
        if not context:
            return base_prompt
        
        augmented_prompt = f"""**REFERENCE CONTEXT FROM STANDARDS AND DOCUMENTATION:**

{context}

---

{base_prompt}

**Important:** Use the reference context above to enhance your analysis with specific standards, guidelines, and best practices. Cross-reference equipment specifications and design requirements with the provided documentation."""
        
        return augmented_prompt
    
    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors"""
        try:
            # Convert to numpy arrays
            a = np.array(vec1)
            b = np.array(vec2)
            
            # Calculate cosine similarity
            dot_product = np.dot(a, b)
            norm_a = np.linalg.norm(a)
            norm_b = np.linalg.norm(b)
            
            if norm_a == 0 or norm_b == 0:
                return 0.0
            
            similarity = dot_product / (norm_a * norm_b)
            return float(similarity)
            
        except Exception as e:
            print(f"[ERROR] Similarity calculation failed: {str(e)}")
            return 0.0
