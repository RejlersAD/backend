"""
AI Invoice Classifier Service
Classifies invoices into Finance, IT, Project, or Admin categories using OpenAI
"""
from openai import OpenAI
from typing import Dict, Optional
import json
import logging
from django.conf import settings

logger = logging.getLogger(__name__)


class InvoiceClassifier:
    """AI-powered invoice classifier using OpenAI GPT"""
    
    def __init__(self):
        """Initialize OpenAI client"""
        self.api_key = getattr(settings, 'OPENAI_API_KEY', None)
        self.model = getattr(settings, 'OPENAI_MODEL', 'gpt-4')
        
        if self.api_key:
            self.client = OpenAI(api_key=self.api_key)
        else:
            logger.warning("OpenAI API key not configured - AI classification will use fallback rules")
            self.client = None
    
    def classify_invoice(self, invoice_text: str, invoice_data: Dict) -> Optional[Dict]:
        """
        Classify invoice using AI
        Returns: {
            'category': 'finance|it|project|admin',
            'confidence': 0.95,
            'reasoning': 'explanation',
            'vendor_name': 'extracted name',
            'invoice_number': 'extracted number',
            'total_amount': 'extracted amount'
        }
        """
        if self.client:
            return self._classify_with_openai(invoice_text, invoice_data)
        else:
            return self._classify_with_rules(invoice_text, invoice_data)
    
    def _classify_with_openai(self, invoice_text: str, invoice_data: Dict) -> Optional[Dict]:
        """Classify using OpenAI API with intelligent data extraction"""
        try:
            prompt = f"""
You are an expert finance invoice analyzer and classifier.

Analyze this invoice and extract ALL key information intelligently.

INVOICE TEXT:
{invoice_text[:2000]}

CRITICAL EXTRACTION RULES:
1. INVOICE NUMBER: Look for these patterns and extract EXACTLY:
   - "Invoice #: XXXXX" or "Invoice Number: XXXXX"
   - "INV-XXXXX" or "INV#XXXXX"
   - Any alphanumeric code near the word "Invoice" at the top of the document
   - Numbers that appear after "Invoice:", "Invoice No:", "Invoice #", "INV:", "Inv No."
   - If multiple numbers found, choose the one closest to the top of the document
   - If NO invoice number found, generate one in format: "INV-" + current timestamp

2. VENDOR NAME: Extract the company name at the very top (usually the first line with Inc/LLC/Ltd/Corp)

3. TOTAL AMOUNT: Look for "Total", "Amount Due", "Grand Total", "Invoice Total"

4. CLASSIFY into ONE category based on invoice content

Categories:
- "IT Invoice": Technology, software, cloud services, hardware, IT consulting, SaaS, licenses
- "Project Invoice": Project expenses, construction, engineering, consultant fees, project materials
- "Accounts/Finance Invoice": Banking, insurance, accounting, taxes, auditing, financial services
- "General/Admin Invoice": Office supplies, utilities, HR, admin services, facility management

RESPOND ONLY WITH VALID JSON (no markdown, no extra text):
{{
    "invoice_number": "MUST extract or generate - NEVER leave empty",
    "vendor_name": "exact company/vendor name from document",
    "total_amount": "numeric value only (e.g., 4850.0)",
    "currency": "currency code (USD/AED/EUR/etc)",
    "invoice_type": "IT Invoice|Project Invoice|Accounts/Finance Invoice|General/Admin Invoice",
    "confidence": 0.95,
    "reasoning": "brief explanation of classification"
}}

IMPORTANT: 
- Invoice number is MANDATORY - extract it carefully from the document
- Look at the ENTIRE text for invoice number patterns
- Common locations: top of document, near "Invoice" heading, after "Invoice #" or "Invoice Number"
"""
            
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are an expert invoice analyzer. Always respond with valid JSON only. Extract exact data from invoices."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=400
            )
            
            result_text = response.choices[0].message.content.strip()
            
            # Remove markdown code blocks if present
            if result_text.startswith('```'):
                result_text = result_text.split('```')[1]
                if result_text.startswith('json'):
                    result_text = result_text[4:]
                result_text = result_text.strip()
            
            # Parse JSON response
            result = json.loads(result_text)
            
            # Map invoice_type to category
            type_mapping = {
                'IT Invoice': 'it',
                'Project Invoice': 'project',
                'Accounts/Finance Invoice': 'finance',
                'General/Admin Invoice': 'admin'
            }
            
            category = type_mapping.get(result.get('invoice_type'), 'admin')
            
            return {
                'category': category,
                'confidence': result.get('confidence', 0.8),
                'reasoning': result.get('reasoning', ''),
                'vendor_name': result.get('vendor_name', invoice_data.get('vendor_name')),
                'invoice_number': result.get('invoice_number', invoice_data.get('invoice_number')),
                'total_amount': result.get('total_amount', invoice_data.get('total_amount')),
                'currency': result.get('currency', invoice_data.get('currency', 'AED'))
            }
            
        except Exception as e:
            logger.error(f"OpenAI classification failed: {e}")
            return self._classify_with_rules(invoice_text, invoice_data)
    
    def _classify_with_rules(self, invoice_text: str, invoice_data: Dict) -> Optional[Dict]:
        """Fallback rule-based classification"""
        text_lower = invoice_text.lower()
        
        # IT keywords
        it_keywords = [
            'software', 'hardware', 'computer', 'server', 'cloud', 'saas',
            'license', 'microsoft', 'aws', 'azure', 'google cloud', 'hosting',
            'domain', 'ssl', 'database', 'api', 'development', 'programming'
        ]
        
        # Project keywords
        project_keywords = [
            'project', 'construction', 'engineering', 'consultant', 'design',
            'architect', 'contractor', 'material', 'equipment', 'installation'
        ]
        
        # Finance keywords
        finance_keywords = [
            'bank', 'insurance', 'accounting', 'tax', 'audit', 'financial',
            'investment', 'loan', 'interest', 'premium'
        ]
        
        # Admin keywords
        admin_keywords = [
            'office', 'supplies', 'stationery', 'utility', 'electricity',
            'water', 'cleaning', 'maintenance', 'rent', 'lease'
        ]
        
        # Count keyword matches
        scores = {
            'it': sum(1 for kw in it_keywords if kw in text_lower),
            'project': sum(1 for kw in project_keywords if kw in text_lower),
            'finance': sum(1 for kw in finance_keywords if kw in text_lower),
            'admin': sum(1 for kw in admin_keywords if kw in text_lower)
        }
        
        # Find category with highest score
        category = max(scores, key=scores.get)
        max_score = scores[category]
        
        # If no matches, default to admin
        if max_score == 0:
            category = 'admin'
            confidence = 0.5
        else:
            # Calculate confidence based on score
            total_keywords = len(it_keywords) + len(project_keywords) + len(finance_keywords) + len(admin_keywords)
            confidence = min(0.95, max_score / 10 + 0.6)
        
        return {
            'category': category,
            'confidence': confidence,
            'reasoning': f'Rule-based classification based on keyword matching (score: {max_score})',
            'vendor_name': invoice_data.get('vendor_name'),
            'invoice_number': invoice_data.get('invoice_number'),
            'total_amount': invoice_data.get('total_amount'),
            'currency': invoice_data.get('currency', 'AED')
        }
