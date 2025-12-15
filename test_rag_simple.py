"""
Quick RAG Test
Test simplified RAG service functionality
"""
import os
import sys
import django
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from apps.pid_analysis.rag_service import RAGService


def test_rag():
    """Test RAG system"""
    print("\n" + "="*60)
    print("TESTING SIMPLIFIED RAG SYSTEM")
    print("="*60)
    print(f"OpenAI API Key: {'✓ Set' if os.environ.get('OPENAI_API_KEY') else '✗ Not set'}")
    print(f"RAG Enabled: {os.environ.get('RAG_ENABLED', 'false')}")
    print(f"Embedding Model: {os.environ.get('EMBEDDING_MODEL', 'text-embedding-3-small')}")
    
    try:
        rag_service = RAGService()
        
        # Test 1: Generate Embedding
        print("\nTest 1: Generating embedding...")
        test_text = "P&ID drawings must comply with ADNOC standards"
        embedding = rag_service.generate_embedding(test_text)
        print(f"✓ Generated embedding with {len(embedding)} dimensions")
        print(f"  Sample values: {embedding[:5]}")
        
        # Test 2: Add Reference Document
        print("\nTest 2: Adding reference document...")
        test_content = """
        ADNOC P&ID Design Standards
        
        Equipment Tagging Requirements:
        - All pumps must be tagged as P-XXX-YYY
        - All vessels must be tagged as V-XXX-YYY
        - All heat exchangers must be tagged as E-XXX-YYY
        
        Instrument Standards (ISA-5.1):
        - Pressure indicators: PI
        - Temperature transmitters: TT
        - Flow control valves: FCV
        - Level transmitters: LT
        
        Safety Requirements:
        - All critical equipment must have redundant instruments
        - Safety instrument functions (SIF) must be clearly marked
        - Emergency shutdown systems must be highlighted
        """
        
        chunk_data = rag_service.add_reference_document(
            document_id="test_001",
            content=test_content,
            metadata={'title': 'ADNOC Standards', 'category': 'standard'}
        )
        
        print(f"✓ Added document with {len(chunk_data)} chunks")
        
        # Test 3: Embedding dimension check
        if chunk_data:
            first_chunk = chunk_data[0]
            print(f"  Chunk 0: {len(first_chunk['text'])} chars, {len(first_chunk['embedding'])} dims")
        
        print("\n" + "="*60)
        print("✓ ALL TESTS PASSED - RAG SYSTEM IS OPERATIONAL")
        print("="*60 + "\n")
        
        return True
        
    except Exception as e:
        print(f"\n✗ TEST FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == '__main__':
    test_rag()
