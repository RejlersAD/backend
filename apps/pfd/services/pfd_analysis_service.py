"""
PFD Analysis Service
AI-powered PFD verification using OpenAI GPT-4 Vision with reference document support
"""
import os
import base64
import io
import json
from datetime import datetime
from typing import Dict, List, Any, Optional
from django.conf import settings
from openai import OpenAI
import fitz  # PyMuPDF
from PIL import Image


class PFDAnalysisService:
    """AI-Powered PFD Verification Service with Reference Document Integration"""
    
    # Fixed PFD verification checks prompt
    PFD_VERIFICATION_PROMPT = """
You are an engineering PFD checker AI.

You must generate the report strictly based on the provided PFD image.
Other documents (BFD, Process Description, Process Design Basis, Equipment Data Sheet, etc.) are for reference only.

Your task is to fully understand the PFD and identify all issues strictly based on the checks listed below.
Do not assume missing information unless it is explicitly required by PFD conventions.

Provide output ONLY in HTML table format with the following columns:
Issue Serial No. | Issue Found | Action Required

The PFD image is provided as an image URL. Do NOT return empty or generic results.
Each reported issue must be traceable to a specific location or element on the PFD.

List of Fixed Checks to be performed on the PFD:

1. Verify drawing number, revision number, project name, and client name are correct and consistent with the Project Reference Document.
2. Verify that all major equipment (vessels, pumps, compressors, heat exchangers, columns, reactors) shown on PFD match the Equipment List.
3. Verify that equipment tag numbers are consistent with project tagging philosophy and Equipment List.
4. Verify that all major process streams are shown with unique stream numbers.
5. Check that stream numbering is consistent throughout the PFD without duplication or omission.
6. Verify that flow direction arrows are correctly shown for all process streams.
7. Verify that material balance consistency is maintained (no unexplained creation or loss of mass across equipment).
8. Check that all major utility connections (steam, cooling water, fuel gas, nitrogen, instrument air) are clearly indicated where required.
9. Verify that operating conditions (pressure, temperature, flow rate) are shown where required as per PFD practice.
10. Verify that heat exchangers show inlet and outlet process streams clearly.
11. Verify that pumps and compressors show suction and discharge streams correctly.
12. Verify that recycle streams are clearly indicated and properly referenced.
13. Verify that all process control loops shown are consistent with PFD-level representation (no detailed P&ID-level instruments).
14. Verify that phase changes (vapor/liquid) are logically represented across equipment.
15. Verify that tie-ins to off-page streams are properly referenced.
16. Verify that major safety-related process flows (relief, vent, flare connections) are logically indicated if applicable at PFD level.
17. Verify that feed and product streams are clearly identified and labeled.
18. Verify that bypasses or alternative flow paths shown on PFD are process-justified.
19. Verify that PFD symbols used are consistent across the drawing.
20. Verify that process units and battery limits are clearly identified.
21. Verify that stream data references match the Stream Data Table when referenced on the PFD.
22. Verify that process description implied by the PFD is logically consistent (no missing essential process steps).
23. Verify notes provided on the PFD for correctness and consistency with the diagram.
24. Verify that equipment counts and parallel trains are correctly represented.
25. Verify that no P&ID-level details (valve sizes, nozzle numbers, hook-up details) are incorrectly shown on the PFD.

Instructions you MUST follow:

1. Do NOT report issues related to drawing readability or image quality.
2. Do NOT report P&ID-level issues (valve orientation, nozzle details, instrument hook-ups).
3. Do NOT assume missing data as an issue unless required by standard PFD practice.
4. Provide a serial number for every issue and reference it clearly.
5. Avoid generic issues; each issue must have a specific technical basis.
6. Do NOT report issues regarding equipment or streams not present on the provided PFD.
7. Do NOT report line list or piping specification issues.
8. Do NOT report control valve bypass sizing issues (PFD level only).
9. Do NOT report alarm or trip set point issues (PFD does not govern this).
10. Do NOT report PSV sizing or detailed relief system design issues.
11. Stream numbers must be verified only if shown on the PFD.
12. Only issues identifiable directly from the PFD image shall be reported.
13. Do NOT suggest design changes, identify issues and require corrective actions.
14. Output MUST be in structured JSON format (not HTML table for API response).

Return your analysis as JSON with this structure:
{
  "drawing_info": {
    "drawing_number": "extracted drawing number",
    "revision": "extracted revision",
    "project_name": "extracted project name",
    "client_name": "extracted client name"
  },
  "issues": [
    {
      "serial_number": 1,
      "issue_found": "Specific issue description",
      "action_required": "Specific corrective action",
      "severity": "critical|major|minor|observation",
      "category": "Equipment|Streams|Control|Documentation|Safety|Material Balance",
      "approval": "Pending",
      "remark": "Pending"
    }
  ],
  "summary": {
    "total_issues": 0,
    "critical_count": 0,
    "major_count": 0,
    "minor_count": 0,
    "observation_count": 0
  }
}

Only answer based on the PFD image provided.
"""
    
    def __init__(self):
        """Initialize OpenAI client"""
        # Get API key from settings
        api_key = None
        
        if hasattr(settings, 'OPENAI_API_KEY'):
            api_key = settings.OPENAI_API_KEY
        elif os.getenv('OPENAI_API_KEY'):
            api_key = os.getenv('OPENAI_API_KEY')
        
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not configured")
        
        self.client = OpenAI(api_key=api_key)
        self.model = "gpt-4o"  # GPT-4 with vision
        self.MAX_TOKENS = 16000
        self.AI_TEMPERATURE = 0.15  # Low temperature for consistent technical analysis
        
        print(f"[PFD_ANALYSIS] Initialized with model: {self.model}")
    
    def analyze_pfd_document(
        self,
        pfd_file,
        reference_documents: Dict[str, Any] = None,
        drawing_metadata: Dict[str, str] = None
    ) -> Dict[str, Any]:
        """
        Analyze PFD document with optional reference documents
        
        Args:
            pfd_file: PFD file object (PDF)
            reference_documents: Dict of reference documents {doc_type: file_path}
            drawing_metadata: Dict with drawing_number, revision, title, etc.
        
        Returns:
            Analysis results with issues, drawing info, and summary
        """
        try:
            print(f"[PFD_ANALYSIS] Starting PFD analysis")
            print(f"[PFD_ANALYSIS] File: {pfd_file.name if hasattr(pfd_file, 'name') else 'Unknown'}")
            print(f"[PFD_ANALYSIS] Reference docs: {len(reference_documents) if reference_documents else 0}")
            
            # Extract PFD pages as images
            pfd_images_base64 = self._extract_pdf_pages(pfd_file)
            print(f"[PFD_ANALYSIS] Extracted {len(pfd_images_base64)} page(s) from PFD")
            
            # Process reference documents if provided
            reference_context = ""
            if reference_documents:
                reference_context = self._process_reference_documents(reference_documents)
                print(f"[PFD_ANALYSIS] Processed reference documents context ({len(reference_context)} chars)")
            
            # Build enhanced prompt with reference context
            full_prompt = self.PFD_VERIFICATION_PROMPT
            
            if reference_context:
                full_prompt += f"\n\nREFERENCE DOCUMENTS CONTEXT:\n{reference_context}\n\n"
                full_prompt += "Use the reference documents above to verify consistency and completeness.\n"
            
            if drawing_metadata:
                full_prompt += f"\n\nDRAWING METADATA PROVIDED:\n"
                full_prompt += f"- Drawing Number: {drawing_metadata.get('drawing_number', 'Not provided')}\n"
                full_prompt += f"- Revision: {drawing_metadata.get('revision', 'Not provided')}\n"
                full_prompt += f"- Title: {drawing_metadata.get('title', 'Not provided')}\n"
                full_prompt += f"- Project: {drawing_metadata.get('project_name', 'Not provided')}\n\n"
            
            # Call OpenAI Vision API
            analysis_result = self._call_vision_api(pfd_images_base64, full_prompt)
            
            print(f"[PFD_ANALYSIS] Analysis completed successfully")
            print(f"[PFD_ANALYSIS] Total issues found: {analysis_result.get('summary', {}).get('total_issues', 0)}")
            
            return analysis_result
            
        except Exception as e:
            print(f"[PFD_ANALYSIS ERROR] {type(e).__name__}: {str(e)}")
            import traceback
            traceback.print_exc()
            raise
    
    def _extract_pdf_pages(self, pdf_file) -> List[str]:
        """Extract PDF pages as base64 encoded images"""
        try:
            # Read PDF file
            if hasattr(pdf_file, 'read'):
                pdf_bytes = pdf_file.read()
                if hasattr(pdf_file, 'seek'):
                    pdf_file.seek(0)  # Reset file pointer
            else:
                with open(pdf_file, 'rb') as f:
                    pdf_bytes = f.read()
            
            # Open PDF with PyMuPDF
            pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")
            images_base64 = []
            
            for page_num in range(len(pdf_document)):
                page = pdf_document[page_num]
                
                # Render page to image at high resolution
                zoom = 2.0  # 200% zoom for better quality
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat)
                
                # Convert to PIL Image
                img_data = pix.tobytes("png")
                img = Image.open(io.BytesIO(img_data))
                
                # Convert to base64
                buffered = io.BytesIO()
                img.save(buffered, format="PNG")
                img_base64 = base64.b64encode(buffered.getvalue()).decode('utf-8')
                images_base64.append(img_base64)
            
            pdf_document.close()
            return images_base64
            
        except Exception as e:
            print(f"[ERROR] Failed to extract PDF pages: {e}")
            raise
    
    def _process_reference_documents(self, reference_docs: Dict[str, Any]) -> str:
        """Process reference documents and extract relevant context"""
        context_parts = []
        
        doc_type_labels = {
            'bfd': 'Block Flow Diagram (BFD)',
            'process_description': 'Process Description',
            'process_design_basis': 'Process Design Basis',
            'operation_control_philosophy': 'Operation & Control Philosophy',
            'scope_of_work': 'Scope of Work',
            'legends_symbols': 'Legends and Symbols',
            'equipment_data_sheet': 'Equipment Data Sheet',
            'other_documents': 'Other Reference Documents'
        }
        
        for doc_type, doc_path in reference_docs.items():
            if doc_path and doc_path != 'null':
                label = doc_type_labels.get(doc_type, doc_type.replace('_', ' ').title())
                context_parts.append(f"- {label}: Available for cross-reference")
        
        if context_parts:
            return "\n".join(context_parts)
        return ""
    
    def _call_vision_api(self, images_base64: List[str], prompt: str) -> Dict[str, Any]:
        """Call OpenAI Vision API with PFD images"""
        try:
            # Build message content with images
            message_content = [{"type": "text", "text": prompt}]
            
            for idx, img_base64 in enumerate(images_base64):
                message_content.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{img_base64}",
                        "detail": "high"
                    }
                })
            
            print(f"[PFD_ANALYSIS] Calling OpenAI API (model: {self.model}) with {len(images_base64)} page(s)...")
            print(f"[PFD_ANALYSIS] Request timestamp: {datetime.now().isoformat()}")
            
            # Call OpenAI API
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are a Senior Process Engineer specializing in PFD verification with expertise in oil & gas process design, equipment specification, and process flow documentation standards."
                    },
                    {
                        "role": "user",
                        "content": message_content
                    }
                ],
                max_tokens=self.MAX_TOKENS,
                temperature=self.AI_TEMPERATURE,
                response_format={"type": "json_object"}
            )
            
            # Parse response
            response_text = response.choices[0].message.content
            print(f"[PFD_ANALYSIS] Received response ({len(response_text)} chars)")
            
            # Parse JSON
            analysis_result = json.loads(response_text)
            
            # Validate and normalize structure
            if 'issues' not in analysis_result:
                analysis_result['issues'] = []
            
            if 'drawing_info' not in analysis_result:
                analysis_result['drawing_info'] = {}
            
            if 'summary' not in analysis_result:
                analysis_result['summary'] = self._generate_summary(analysis_result['issues'])
            
            return analysis_result
            
        except json.JSONDecodeError as e:
            print(f"[ERROR] Failed to parse JSON response: {e}")
            print(f"[ERROR] Response text: {response_text[:500]}...")
            raise ValueError(f"Invalid JSON response from AI: {str(e)}")
        
        except Exception as e:
            print(f"[ERROR] Vision API call failed: {type(e).__name__}: {str(e)}")
            raise
    
    def _generate_summary(self, issues: List[Dict]) -> Dict[str, int]:
        """Generate summary statistics from issues"""
        summary = {
            'total_issues': len(issues),
            'critical_count': 0,
            'major_count': 0,
            'minor_count': 0,
            'observation_count': 0
        }
        
        for issue in issues:
            severity = issue.get('severity', 'observation').lower()
            if severity == 'critical':
                summary['critical_count'] += 1
            elif severity == 'major':
                summary['major_count'] += 1
            elif severity == 'minor':
                summary['minor_count'] += 1
            else:
                summary['observation_count'] += 1
        
        return summary
    
    def generate_report_summary(self, issues: List[Dict]) -> Dict[str, int]:
        """Generate report summary with approval status counts"""
        summary = {
            'approved_count': 0,
            'ignored_count': 0,
            'pending_count': 0
        }
        
        for issue in issues:
            status = issue.get('status', 'pending').lower()
            if status == 'approved':
                summary['approved_count'] += 1
            elif status == 'ignored':
                summary['ignored_count'] += 1
            else:
                summary['pending_count'] += 1
        
        return summary
