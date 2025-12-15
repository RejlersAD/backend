"""
Test RAG System
Quick test to verify RAG service functionality
"""
import os
import sys
import django

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from apps.pid_analysis.rag_service import RAGService
from apps.pid_analysis.document_processor import DocumentProcessor


def test_embedding_generation():
    """Test embedding generation"""
    print("\n" + "="*60)
    print("TEST 1: Embedding Generation")
    print("="*60)
    
    try:
        rag_service = RAGService()
        
        test_text = "P&ID drawings must comply with ADNOC standards for oil and gas facilities."
        embedding = rag_service.generate_embedding(test_text)
        
        print(f"✓ Generated embedding with {len(embedding)} dimensions")
        print(f"  Sample values: {embedding[:5]}...")
        return True
    except Exception as e:
        print(f"✗ Failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def test_document_processing():
    """Test document text chunking"""
    print("\n" + "="*60)
    print("TEST 2: Document Processing")
    print("="*60)
    
    try:
        processor = DocumentProcessor()
        
        test_text = """
        P&ID Design Standards for Oil & Gas Facilities
        
        1. Equipment Tagging
        All equipment must follow ADNOC tagging standards:
        - Pumps: P-XXX-YYY
        - Vessels: V-XXX-YYY
        - Heat Exchangers: E-XXX-YYY
        
        2. Instrument Standards
        Instrument tags must comply with ISA-5.1 standard:
        - Pressure indicators: PI
        - Temperature transmitters: TT
        - Flow control valves: FCV
        
        3. Line Numbering
        Process lines must include:
        - Line number
        - Size
        - Material specification
        - Insulation requirements
        """
        
        chunks = processor.chunk_text(test_text, chunk_size=200, chunk_overlap=50)
        
        print(f"✓ Created {len(chunks)} chunks from {len(test_text)} characters")
        for i, chunk in enumerate(chunks):
            print(f"  Chunk {i+1}: {len(chunk)} chars - {chunk[:60]}...")
        return True
    except Exception as e:
        print(f"✗ Failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def test_vector_database():
    """Test vector database operations"""
    print("\n" + "="*60)
    print("TEST 3: Vector Database Operations")
    print("="*60)
    
    try:
        rag_service = RAGService()
        
        # Add test document
        test_content = """
        ADNOC P&ID Review Checklist
        
        Equipment Verification:
        - Verify all equipment tags are unique
        - Check equipment data sheets match P&ID
        - Confirm material specifications
        
        Instrument Verification:
        - Verify instrument tags follow ISA-5.1
        - Check instrument specifications
        - Confirm safety instrument functions (SIF)
        
        Line Verification:
        - Verify line numbers are unique
        - Check line sizes and materials
        - Confirm insulation requirements
        """
        
        print("  Adding test document to vector database...")
        vector_ids = rag_service.add_reference_document(
            document_id="test_doc_001",
            content=test_content,
            metadata={
                'title': 'ADNOC P&ID Review Checklist',
                'category': 'standard',
                'filename': 'test_checklist.txt'
            }
        )
        print(f"✓ Added document with {len(vector_ids)} chunks")
        
        # Test retrieval
        print("\n  Testing context retrieval...")
        query = "What are the requirements for equipment verification in P&ID drawings?"
        context = rag_service.retrieve_context(query, top_k=3)
        
        print(f"✓ Retrieved context ({len(context)} chars):")
        print(f"  {context[:300]}...")
        
        return True
    except Exception as e:
        print(f"✗ Failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def test_augmented_prompt():
    """Test augmented prompt generation"""
    print("\n" + "="*60)
    print("TEST 4: Augmented Prompt Generation")
    print("="*60)
    
    try:
        rag_service = RAGService()
        
        base_prompt = "Analyze this P&ID drawing and identify issues."
        query = "equipment tagging standards"
        
        augmented_prompt = rag_service.build_augmented_prompt(base_prompt, query)
        
        print(f"✓ Generated augmented prompt ({len(augmented_prompt)} chars)")
        print(f"  Base prompt: {len(base_prompt)} chars")
        print(f"  Augmented: {len(augmented_prompt)} chars")
        print(f"\n  Preview:")
        print(f"  {augmented_prompt[:400]}...")
        
        return True
    except Exception as e:
        print(f"✗ Failed: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Run all RAG tests"""
    print("\n" + "="*60)
    print("RAG SYSTEM TEST SUITE")
    print("="*60)
    
    # Check configuration
    print("\nConfiguration:")
    print(f"  RAG_ENABLED: {os.environ.get('RAG_ENABLED', 'false')}")
    print(f"  VECTOR_DB_TYPE: {os.environ.get('VECTOR_DB_TYPE', 'chromadb')}")
    print(f"  EMBEDDING_MODEL: {os.environ.get('EMBEDDING_MODEL', 'sentence-transformers/all-MiniLM-L6-v2')}")
    
    # Run tests
    tests = [
        test_embedding_generation,
        test_document_processing,
        test_vector_database,
        test_augmented_prompt
    ]
    
    results = []
    for test in tests:
        try:
            result = test()
            results.append(result)
        except Exception as e:
            print(f"\n✗ Test failed with exception: {str(e)}")
            results.append(False)
    
    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    passed = sum(results)
    total = len(results)
    print(f"Passed: {passed}/{total}")
    
    if passed == total:
        print("\n✓ All tests passed! RAG system is operational.")
    else:
        print(f"\n✗ {total - passed} test(s) failed. Please review errors above.")
    
    print("="*60 + "\n")


if __name__ == '__main__':
    main()
