"""
P&ID Validation Pipeline - Main Orchestrator
Hybrid: Discriminative Extraction + Deterministic Rules + Controlled AI
"""
from typing import Dict, List, Any
from .extractor import PIDExtractor
from .rules import RuleEngine
from .agents import AgentOrchestrator


class PIDValidationPipeline:
    """
    Main validation pipeline orchestrator
    
    Architecture:
    1. Discriminative Extraction (OCR + Regex) - Source of Truth
    2. Deterministic Rule Engine - No hallucination
    3. Agentic LLM Validation - Controlled, grounded
    
    Output: IDENTICAL format to existing system
    """
    
    def __init__(self, pdf_dpi: int = 300):
        print("[PIPELINE] Initializing P&ID Validation Pipeline...")
        
        # Initialize components
        self.extractor = PIDExtractor(pdf_dpi=pdf_dpi)
        self.rule_engine = RuleEngine()
        self.agent_orchestrator = AgentOrchestrator()
        
        print("[PIPELINE] Pipeline initialized with 3 layers:")
        print("  1. Discriminative Extraction (OCR + Regex)")
        print("  2. Deterministic Rule Engine")
        print("  3. Controlled Agentic LLM")
    
    def validate(
        self, 
        pdf_file, 
        drawing_number: str = None,
        images_base64: List[str] = None
    ) -> Dict[str, Any]:
        """
        Run complete validation pipeline
        
        Args:
            pdf_file: P&ID PDF file
            drawing_number: Drawing number (optional)
            images_base64: Base64 encoded images (optional, for LLM)
        
        Returns:
            {
                'issues': List[Dict],  # SAME format as existing system
                'metadata': {
                    'document_id': str,
                    'total_lines': int,
                    'total_equipment': int,
                    'total_instruments': int,
                    'extraction_method': 'hybrid',
                    'deterministic_issues': int,
                    'llm_issues': int
                }
            }
        """
        print(f"\n{'='*80}")
        print(f"[PIPELINE] STARTING VALIDATION - Drawing: {drawing_number or 'Unknown'}")
        print(f"{'='*80}\n")
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 1: DISCRIMINATIVE EXTRACTION (Source of Truth)
        # ═══════════════════════════════════════════════════════════════════
        print("[PIPELINE] STEP 1: Discriminative Extraction...")
        extracted_data = self.extractor.extract(pdf_file, drawing_number)
        
        document_id = extracted_data['document_id']
        print(f"[PIPELINE] Context isolated - Document ID: {document_id}")
        print(f"[PIPELINE] Extracted:")
        print(f"  - Lines: {len(extracted_data['lines'])}")
        print(f"  - Equipment: {len(extracted_data['equipment'])}")
        print(f"  - Instruments: {len(extracted_data['instruments'])}")
        print(f"  - Active Notes: {len(extracted_data['notes'])}")
        print(f"  - Deleted Notes: {len(extracted_data['deleted_notes'])}")
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 2: DETERMINISTIC RULE ENGINE
        # ═══════════════════════════════════════════════════════════════════
        print(f"\n[PIPELINE] STEP 2: Deterministic Rule Validation...")
        rule_issues = self.rule_engine.validate(extracted_data)
        print(f"[PIPELINE] Deterministic rules found {len(rule_issues)} issue(s)")
        
        # ═══════════════════════════════════════════════════════════════════
        # STEP 3: CONTROLLED AGENTIC LLM VALIDATION
        # ═══════════════════════════════════════════════════════════════════
        print(f"\n[PIPELINE] STEP 3: Controlled Agentic Validation...")
        
        if images_base64 is None:
            # Generate images if not provided
            images_base64 = self._generate_images(pdf_file)
        
        final_issues = self.agent_orchestrator.run(
            extracted_data,
            rule_issues,
            images_base64
        )
        
        # ═══════════════════════════════════════════════════════════════════
        # FINAL OUTPUT (Matches existing system format)
        # ═══════════════════════════════════════════════════════════════════
        result = {
            'issues': final_issues,
            'metadata': {
                'document_id': document_id,
                'drawing_number': drawing_number or 'Unknown',
                'total_lines': len(extracted_data['lines']),
                'total_equipment': len(extracted_data['equipment']),
                'total_instruments': len(extracted_data['instruments']),
                'total_notes': len(extracted_data['notes']),
                'extraction_method': 'hybrid_discriminative_agentic',
                'deterministic_issues': len(rule_issues),
                'llm_issues': len(final_issues) - len(rule_issues),
                'deterministic': True  # Output is deterministic
            }
        }
        
        print(f"\n{'='*80}")
        print(f"[PIPELINE] VALIDATION COMPLETE")
        print(f"  - Total Issues: {len(final_issues)}")
        print(f"  - Deterministic: {len(rule_issues)}")
        print(f"  - LLM Additional: {len(final_issues) - len(rule_issues)}")
        print(f"  - Document ID: {document_id}")
        print(f"{'='*80}\n")
        
        return result
    
    def _generate_images(self, pdf_file) -> List[str]:
        """Generate base64 images from PDF (for LLM)"""
        import base64
        import fitz  # PyMuPDF
        from PIL import Image
        import io
        
        images_base64 = []
        
        try:
            # Handle Django FieldFile or file path
            if hasattr(pdf_file, 'read'):
                pdf_bytes = pdf_file.read()
                if hasattr(pdf_file, 'seek'):
                    pdf_file.seek(0)
            else:
                with open(pdf_file, 'rb') as f:
                    pdf_bytes = f.read()
            
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            
            for page_num in range(min(len(doc), 2)):  # Max 2 pages for token economy
                page = doc[page_num]
                pix = page.get_pixmap(dpi=200)  # Lower DPI for token economy
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                
                # Convert to base64
                buffer = io.BytesIO()
                img.save(buffer, format='PNG', optimize=True)
                buffer.seek(0)
                img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
                images_base64.append(img_base64)
            
            doc.close()
            
        except Exception as e:
            print(f"[PIPELINE ERROR] Image generation failed: {e}")
        
        return images_base64
