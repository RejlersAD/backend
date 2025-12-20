"""
PDF Comment Extraction Service for CRS
Extracts red text comments and yellow box callouts from PDF documents
"""

import fitz
import os
import re
from typing import List, Dict, Tuple, Optional
from datetime import datetime


class PDFCommentExtractor:
    """
    Professional PDF comment extraction service
    Detects red comments, yellow boxes, and annotations
    """
    
    def __init__(self, debug_mode: bool = False):
        self.debug_mode = debug_mode
        self.RED_THRESHOLD = 0.7
        self.MIN_RED_COMPONENT = 0.5
        self.YELLOW_THRESHOLD = 0.6
    
    def is_red_color(self, color: Tuple[float, float, float]) -> bool:
        """Detect if color is red with multiple validation checks"""
        if not color or len(color) < 3:
            return False
        
        r, g, b = color[0], color[1], color[2]
        
        # Normalize if values > 1
        if r > 1.0 or g > 1.0 or b > 1.0:
            r, g, b = r/255.0, g/255.0, b/255.0
        
        # Skip grayscale
        if abs(r - g) < 0.05 and abs(g - b) < 0.05:
            return False
        
        # Primary red detection
        if r > 0.5 and r > g and r > b:
            if (r - g) > 0.1 and (r - b) > 0.1:
                return True
        
        # Secondary red detection
        if r > 0.3 and r > g * 1.3 and r > b * 1.3:
            if (r - g) > 0.08 and (r - b) > 0.08:
                return True
        
        # Bright red
        if r > 0.7 and g < 0.4 and b < 0.4:
            return True
        
        # General red tone
        if r > 0.4 and r > (g + b) / 1.5:
            if (r - max(g, b)) > 0.15:
                return True
        
        # Reddish hue
        if r > 0.5 and (r - g) > 0.2 and r > b + 0.1:
            return True
        
        return False
    
    def is_yellow_color(self, color: Tuple[float, float, float]) -> bool:
        """Detect if color is yellow"""
        if not color or len(color) < 3:
            return False
        
        r, g, b = color[0], color[1], color[2]
        
        # Normalize if values > 1
        if r > 1.0 or g > 1.0 or b > 1.0:
            r, g, b = r/255.0, g/255.0, b/255.0
        
        # Primary yellow detection
        if g > self.YELLOW_THRESHOLD and r > self.YELLOW_THRESHOLD:
            if (r + g) / 2 > b + 0.2:
                if abs(r - g) < 0.3:
                    return True
        
        # Bright yellow
        if r > 0.8 and g > 0.8 and b < 0.5:
            return True
        
        # Medium yellow
        if r > 0.6 and g > 0.6 and b < 0.4:
            if abs(r - g) < 0.2:
                return True
        
        return False
    
    def is_technical_drawing_element(self, text: str) -> bool:
        """Filter out technical drawing elements"""
        if not text or not text.strip():
            return False
        
        text_stripped = text.strip()
        text_lower = text_stripped.lower()
        
        # AutoCAD elements
        if 'autocad' in text_lower or 'shx text' in text_lower or 'shx' in text_lower:
            return True
        
        # Pure numbers or dimensions
        if re.match(r'^[\d\s\.\/\-]+$', text_stripped):
            if len(text_stripped.split()) <= 2:
                return True
        
        # Elevation markers
        if re.search(r'\b(EL|el|El|RACK)\s*\.?\s*\d+', text_stripped, re.IGNORECASE):
            words = text_stripped.split()
            if len(words) <= 4:
                return True
        
        # Drawing codes
        if re.match(r'^[A-Z]+\d+[\-\d]*$', text_stripped):
            if len(text_stripped) <= 20:
                return True
        
        # Technical codes with elevations
        if re.match(r'^[A-Z0-9\s\.\/\-]+$', text_stripped):
            words = text_stripped.split()
            if len(words) <= 4:
                if re.search(r'(EL|el|RACK|\.\d+|\d+\.\d+)', text_stripped, re.IGNORECASE):
                    return True
                if re.match(r'^[\d\s\.\/\-]+$', text_stripped):
                    return True
        
        # Elevation patterns
        if re.match(r'^[/\-]?\s*(EL|el|RACK)\s*\.?\s*\d+', text_stripped, re.IGNORECASE):
            return True
        
        return False
    
    def clean_comment_text(self, text: str) -> Optional[str]:
        """Clean comment text by removing annotation type labels"""
        if self.is_technical_drawing_element(text):
            return None
        
        annotation_types = [
            'Text Box', 'Callout', 'Call Out', 'Free Text',
            'Note', 'Ellipse', 'Rectangle', 'Line', 'Highlight'
        ]
        
        original_text = text
        original_length = len(text)
        
        # Remove "Name AnnotationType" patterns
        for ann_type in annotation_types:
            pattern = r'^((Mr|Mrs|Ms|Dr|Prof|Miss|Sir|Madam)\s+)?[A-Z][a-z]+\s+[A-Z][a-z]+(\s+[A-Z][a-z]+)?\s+' + re.escape(ann_type) + r'\s+'
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)
            pattern = r'^((Mr|Mrs|Ms|Dr|Prof|Miss|Sir|Madam)\s+)?[A-Z][a-z]+\s+[A-Z][a-z]+(\s+[A-Z][a-z]+)?' + re.escape(ann_type) + r'\s+'
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        
        # Remove standalone annotation type prefixes
        for ann_type in annotation_types:
            pattern = r'^' + re.escape(ann_type) + r'\s+'
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)
        
        # Handle name prefixes
        name_match = re.match(r'^((Mr|Mrs|Ms|Dr|Prof|Miss|Sir|Madam)\s+)?([A-Z][a-z]+\s+){1,3}', text)
        if name_match:
            potential_name = name_match.group(0).strip()
            remaining_after_name = text[len(name_match.group(0)):].strip()
            
            if len(potential_name.split()) <= 4:
                if any(remaining_after_name.lower().startswith(ann.lower()) for ann in annotation_types):
                    text = remaining_after_name
                    for ann_type in annotation_types:
                        if text.lower().startswith(ann_type.lower()):
                            text = text[len(ann_type):].strip()
        
        # Preserve original if too much was removed
        if len(text) < original_length * 0.3 and original_length > 15:
            text = original_text
            
            for ann_type in annotation_types:
                pattern = r'^[A-Z][a-z]+\s+[A-Z][a-z]+(\s+[A-Z][a-z]+)?\s+' + re.escape(ann_type) + r'\s+'
                text = re.sub(pattern, '', text, flags=re.IGNORECASE)
            
            for ann_type in annotation_types:
                if text.lower().startswith(ann_type.lower() + ' '):
                    text = text[len(ann_type):].strip()
        
        # Clean whitespace
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\n+', ' ', text)
        text = text.strip()
        
        # Final cleanup
        for ann_type in annotation_types:
            if text.lower().startswith(ann_type.lower() + ' '):
                text = text[len(ann_type):].strip()
            elif text.lower() == ann_type.lower():
                text = ''
        
        return text if text else None
    
    def parse_clause_number(self, text: str) -> str:
        """Extract clause number from comment text"""
        patterns = [
            r'[Cc]lause\s+(\d+[\.\-]?\d*)',
            r'^(\d+[\.\-]\d+)',
            r'(\d+[\.\-]\d+)',
            r'[Cc]l\.\s*(\d+[\.\-]?\d*)',
            r'[§§]\s*(\d+[\.\-]?\d*)',
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                clause = match.group(1)
                clause = clause.strip('.')
                return clause
        
        return ""
    
    def extract_comments_from_pdf(self, pdf_path: str) -> List[Dict]:
        """
        Main extraction method
        Returns list of extracted comments with metadata
        """
        if not os.path.exists(pdf_path):
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        
        pdf_path = os.path.abspath(pdf_path)
        doc = fitz.open(pdf_path)
        comments = []
        seen_texts = set()
        
        print(f"📄 Processing PDF: {pdf_path}")
        print(f"📑 Total pages: {len(doc)}")
        print("🔍 Extracting comments...")
        
        for page_num in range(len(doc)):
            page = doc[page_num]
            page_count = 0
            
            # Process annotations
            try:
                annots = page.annots()
                if annots:
                    for annot in annots:
                        try:
                            info = annot.info
                            content = info.get("content", "").strip()
                            title = info.get("title", "").strip()
                            subject = info.get("subject", "").strip()
                            
                            # Combine annotation text
                            parts = []
                            if title and title.strip():
                                parts.append(title.strip())
                            if subject and subject.strip():
                                parts.append(subject.strip())
                            if content and content.strip():
                                parts.append(content.strip())
                            
                            combined_text = " ".join(parts).strip()
                            
                            # Get annotation type
                            annot_type = annot.type[1] if hasattr(annot.type, '__getitem__') else str(annot.type)
                            is_shape_annotation = annot_type not in ['Text', 'FreeText', 'Highlight', 'Note', 'Comment', 'Callout', 'Link', 'Widget']
                            
                            if combined_text or is_shape_annotation:
                                colors = annot.colors
                                is_red_annot = False
                                is_yellow_box = False
                                rgb = (0, 0, 0)
                                comment_type = "unknown"
                                
                                # Check colors
                                if colors:
                                    # Check fill color
                                    fill = colors.get("fill")
                                    if fill and isinstance(fill, (list, tuple)) and len(fill) >= 3:
                                        r, g, b = fill[0], fill[1], fill[2]
                                        if r > 1.0 or g > 1.0 or b > 1.0:
                                            r, g, b = r/255.0, g/255.0, b/255.0
                                        rgb = (r, g, b)
                                        
                                        if self.is_yellow_color(rgb):
                                            is_yellow_box = True
                                            comment_type = "yellow_box"
                                        elif self.is_red_color(rgb):
                                            is_red_annot = True
                                            comment_type = "red_comment"
                                    
                                    # Check stroke color if not found
                                    if not is_yellow_box and not is_red_annot:
                                        stroke = colors.get("stroke")
                                        if stroke and isinstance(stroke, (list, tuple)) and len(stroke) >= 3:
                                            r, g, b = stroke[0], stroke[1], stroke[2]
                                            if r > 1.0 or g > 1.0 or b > 1.0:
                                                r, g, b = r/255.0, g/255.0, b/255.0
                                            rgb = (r, g, b)
                                            
                                            if self.is_yellow_color(rgb):
                                                is_yellow_box = True
                                                comment_type = "yellow_box"
                                            elif self.is_red_color(rgb):
                                                is_red_annot = True
                                                comment_type = "red_comment"
                                
                                is_comment_type = annot_type in ['Text', 'FreeText', 'Highlight', 'Note', 'Comment', 'Callout']
                                
                                # Store if relevant
                                if is_yellow_box or is_red_annot or (is_comment_type and combined_text) or is_shape_annotation:
                                    if is_yellow_box:
                                        comment_type = "yellow_box"
                                    elif is_red_annot:
                                        comment_type = "red_comment"
                                    elif is_shape_annotation:
                                        comment_type = "shape"
                                    elif is_comment_type:
                                        comment_type = "annotation"
                                    
                                    display_text = combined_text
                                    if is_shape_annotation and not display_text:
                                        display_text = f"[{annot_type}]"
                                    
                                    if display_text or is_shape_annotation:
                                        bbox = list(annot.rect) if hasattr(annot, 'rect') else [0, 0, 0, 0]
                                        bbox_str = f"{int(bbox[0])}_{int(bbox[1])}_{int(bbox[2])}_{int(bbox[3])}" if len(bbox) >= 4 else "0_0_0_0"
                                        text_key = f"{page_num}_{comment_type}_{bbox_str}_{display_text[:50] if display_text else annot_type}"
                                        
                                        if text_key not in seen_texts:
                                            seen_texts.add(text_key)
                                            comments.append({
                                                "text": display_text if display_text else f"[{annot_type}]",
                                                "page": page_num + 1,
                                                "bbox": bbox,
                                                "color": rgb if rgb != (0,0,0) else (1.0, 1.0, 0.0) if is_yellow_box else (1.0, 0.0, 0.0),
                                                "type": comment_type,
                                                "source": "annotation"
                                            })
                                            page_count += 1
                                            type_label = "🟡 Yellow box" if is_yellow_box else "🔴 Red comment" if is_red_annot else f"📌 {annot_type.lower()}" if is_shape_annotation else "💬 Annotation"
                                            if self.debug_mode:
                                                print(f"  {type_label} on page {page_num + 1}: {display_text[:80] if display_text else f'[{annot_type}]'}...")
                        except Exception as e:
                            if self.debug_mode:
                                print(f"  ⚠️ Annotation error: {e}")
            except Exception as e:
                if self.debug_mode:
                    print(f"  ⚠️ Error processing annotations on page {page_num + 1}: {e}")
            
            # Process red text spans
            try:
                text_dict = page.get_text("rawdict")
                
                for block in text_dict.get("blocks", []):
                    if "lines" in block:
                        for line in block["lines"]:
                            for span in line.get("spans", []):
                                text = span.get("text", "").strip()
                                if not text or len(text) < 5:
                                    continue
                                
                                color = span.get("color", 0)
                                
                                # Parse color
                                if isinstance(color, int) and color > 0:
                                    r = ((color >> 16) & 0xFF) / 255.0
                                    g = ((color >> 8) & 0xFF) / 255.0
                                    b = (color & 0xFF) / 255.0
                                elif isinstance(color, (list, tuple)) and len(color) >= 3:
                                    r, g, b = color[0], color[1], color[2]
                                    if r > 1.0 or g > 1.0 or b > 1.0:
                                        r, g, b = r/255.0, g/255.0, b/255.0
                                else:
                                    r, g, b = 0, 0, 0
                                
                                if self.is_red_color((r, g, b)):
                                    text_lower = text.lower()
                                    
                                    # Skip document structure elements
                                    skip_if = [
                                        'adnoc', 'document no', 'contractor', 'project', 'package',
                                        'process design', 'introduction', 'purpose', 'terminology',
                                        'references', 'description', 'philosophy', 'data', 'software',
                                        'appendix', 'table of contents', 'revision control'
                                    ]
                                    
                                    is_document_content = any(skip in text_lower for skip in skip_if)
                                    
                                    # Check if looks like a comment
                                    looks_like_comment = (
                                        len(text.split()) <= 25 and
                                        (text.endswith('.') or text.endswith('?') or 
                                         any(word in text_lower for word in ['shall', 'must', 'should', 'required', 
                                                                             'include', 'consider', 'update', 'provide', 
                                                                             'ensure', 'note', 'comment', 'review']))
                                    )
                                    
                                    if not is_document_content and looks_like_comment:
                                        bbox = span.get("bbox", [0, 0, 0, 0])
                                        bbox_str = f"{int(bbox[0])}_{int(bbox[1])}_{int(bbox[2])}_{int(bbox[3])}" if len(bbox) >= 4 else "0_0_0_0"
                                        text_key = f"{page_num}_red_span_{bbox_str}_{text[:50]}"
                                        
                                        if text_key not in seen_texts:
                                            seen_texts.add(text_key)
                                            comments.append({
                                                "text": text,
                                                "page": page_num + 1,
                                                "bbox": bbox,
                                                "color": (r, g, b),
                                                "type": "red_comment",
                                                "source": "text_span"
                                            })
                                            page_count += 1
                                            if self.debug_mode:
                                                print(f"  🔴 Red text on page {page_num + 1}: {text[:80]}...")
            except Exception as e:
                if self.debug_mode:
                    print(f"  ⚠️ Error processing text spans on page {page_num + 1}: {e}")
            
            if page_count > 0:
                print(f"  ✅ Page {page_num + 1}: Found {page_count} comment(s)")
        
        doc.close()
        
        print(f"\n📊 Extraction Summary:")
        print(f"  Total comments: {len(comments)}")
        print(f"  🔴 Red comments: {sum(1 for c in comments if c['type'] == 'red_comment')}")
        print(f"  🟡 Yellow boxes: {sum(1 for c in comments if c['type'] == 'yellow_box')}")
        print(f"  📌 Other annotations: {sum(1 for c in comments if c['type'] not in ['red_comment', 'yellow_box'])}")
        
        return comments
    
    def process_extracted_comments(self, comments: List[Dict]) -> List[Dict]:
        """
        Process and clean extracted comments
        Returns cleaned comments ready for database storage
        """
        processed_data = []
        
        for item in comments:
            # Skip technical drawing elements
            if self.is_technical_drawing_element(item['text']):
                continue
            
            # Clean text
            text = self.clean_comment_text(item['text'])
            
            if text is None or not text or len(text.strip()) < 3:
                continue
            
            # Parse clause number
            clause = self.parse_clause_number(text)
            
            processed_data.append({
                'text': text,
                'page': item['page'],
                'clause': clause,
                'type': item.get('type', 'unknown'),
                'color': item.get('color', (0, 0, 0)),
                'bbox': item.get('bbox', [0, 0, 0, 0]),
                'source': item.get('source', 'unknown')
            })
        
        return processed_data
