# CRS Comment Extraction Logic Documentation

**Standalone Documentation - No Git Dependencies**

This document contains all the logic for CRS (Comment Resolution Sheet) PDF comment extraction, cleaning, and processing.

---

## 🎯 System Overview

The CRS system extracts reviewer comments from PDF files and intelligently cleans them using:
1. **PyMuPDF (fitz)** - PDF annotation extraction
2. **OpenAI GPT-3.5-turbo** - Intelligent comment cleaning
3. **Color detection** - Yellow boxes and red comments
4. **Rule-based filtering** - Technical drawing element removal

---

## 📋 Main Components

### 1. Comment Extraction (comment_extractor.py)

**Purpose**: Extract comments from PDF annotations and colored text

**Color Detection Logic**:

```python
def is_yellow_color(color):
    """Yellow detection: High R & G, Low B"""
    r, g, b = color[0], color[1], color[2]
    # Normalize to 0-1 if needed
    if r > 1.0: r, g, b = r/255.0, g/255.0, b/255.0
    
    # Primary yellow: both r and g > 0.6, b < 0.5
    if r > 0.8 and g > 0.8 and b < 0.5:
        return True
    if r > 0.6 and g > 0.6 and b < 0.4 and abs(r - g) < 0.2:
        return True
    return False

def is_red_color(color):
    """Red detection: High R, Low G & B"""
    r, g, b = color[0], color[1], color[2]
    if r > 1.0: r, g, b = r/255.0, g/255.0, b/255.0
    
    # Primary red: r > 0.5 and dominant
    if r > 0.7 and g < 0.4 and b < 0.4:
        return True
    if r > 0.5 and (r - g) > 0.1 and (r - b) > 0.1:
        return True
    return False
```

**Technical Element Filtering**:

```python
def is_technical_drawing_element(text):
    """Filters AutoCAD technical elements"""
    text_lower = text.lower()
    
    # AutoCAD patterns
    if 'autocad' in text_lower or 'shx text' in text_lower:
        return True
    
    # Pure numbers/dimensions: "123.45", "100/200"
    if re.match(r'^[\d\s\.\/\-]+$', text.strip()):
        return True
    
    # Elevation codes: "EL.100", "RACK.100"
    if re.search(r'\b(EL|RACK)\s*\.?\s*\d+', text, re.IGNORECASE):
        return True
    
    # Technical codes: "P100", "MC-42"
    if re.match(r'^[A-Z]+\d+[\-\d]*$', text.strip()):
        return True
    
    return False
```

**Extraction Process**:

```python
def extract_reviewer_comments(pdf_buffer, apply_cleaning=True):
    """Main extraction function"""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    comments = []
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        
        # Extract annotations
        annotations = page.annots()
        for annot in annotations:
            # Get comment text
            popup_text = annot.get_text("text").strip()
            content = annot.info.get("content", "").strip()
            title = annot.info.get("title", "").strip()  # Reviewer name
            
            actual_comment = popup_text if popup_text else content
            
            # Check colors
            colors = annot.colors
            fill = colors.get("fill")
            if fill:
                r, g, b = fill[0]/255, fill[1]/255, fill[2]/255
                is_yellow = is_yellow_color((r, g, b))
                is_red = is_red_color((r, g, b))
            
            # Create comment object
            comment = ReviewerComment()
            comment.comment_text = actual_comment
            comment.page_number = page_num + 1
            comment.reviewer_name = title if title else "Not Provided"
            comment.comment_type = "YELLOW_BOX" if is_yellow else "RED_COMMENT" if is_red else "ANNOTATION"
            
            comments.append(comment)
    
    # Apply intelligent cleaning
    if apply_cleaning:
        cleaner = get_comment_cleaner()
        cleaned_comments = []
        for comment in comments:
            result = cleaner.clean_comment(comment.comment_text)
            if not result.should_skip:
                comment.comment_text = result.cleaned_text
                cleaned_comments.append(comment)
        comments = cleaned_comments
    
    return comments
```

---

### 2. Comment Cleaning (comment_cleaner.py)

**Purpose**: Use OpenAI to intelligently clean comment text

**OpenAI Configuration**:

```python
{
    "model": "gpt-3.5-turbo",
    "max_tokens": 500,  # Handles long multi-line comments with bullet lists
    "temperature": 0.1,  # Consistent output
    "enabled": True,
    "fallback_to_rules": True
}
```

**Main OpenAI Prompt**:

```
CRITICAL: Keep COMPLETE comments. Remove names and AutoCAD/Typewriter patterns.

Do TWO things:
1. If this contains ONLY AutoCAD/technical elements with NO comment content 
   (like "Typewriter 166", "SHX Text", pure dimensions), return "SKIP"
2. For everything else, remove from the START: 
   (a) ALL names
   (b) ALL Typewriter patterns
   (c) ALL annotation labels
   BUT KEEP ENTIRE comment including all lines

AUTOCAD/TECHNICAL ELEMENTS TO REMOVE:
- "Typewriter" in ANY form: "Typewriter 166", "Typewriter NC", "ttypewriter"
- "SHX Text", "AutoCAD SHX"
- Pure numbers/dimensions: "123.45", "100/200"
- Elevations: "EL.107.000", "/EL.109.000"

CRITICAL RULES:
1. If text contains ONLY AutoCAD/Typewriter patterns with NO actual comment, return "SKIP"
2. If text has a comment, remove from START: names + Typewriter + annotation labels
3. Examples: "Sreejith Rajeev Typewriter 166" → "SKIP" (no comment after removal)
4. Examples: "Sreejith Rajeev Typewriter 166 Update design" → "Update design"

NAME PATTERNS TO RECOGNIZE AND REMOVE (at start only):
- Hindu names: "Dipak Kantilal", "Sreejith Rajeev", "Manoj Trivedi", "Krishna Das"
- Muslim names: "Mohammed Al Ammari", "Ahmed Ali", "Hassan Rahman"
- Indian names: Any Indian name pattern (first + last name)
- Western names: "John Smith", "Maria Garcia"

PATTERNS TO REMOVE (ONLY at the start):
1. Name prefixes: "Sreejith Rajeev", "Arinya Dashna", "Subrata"
2. Typewriter patterns: "Typewriter 166", "Typewriter NC", "ttypewriter"
3. Structured prefixes: "Comment by Subrata (Process):"
4. With titles: "Mr Manoj Trivedi", "Dr Rajesh Kumar"
5. Annotation labels: "Callout", "Free Text", "Note", "Text Box"
6. Combinations: "Sreejith Rajeev Typewriter 166", "Name Callout"

WHAT TO KEEP:
- Names in middle/end of comments (they're part of content)
- All actual comment text after the prefix
- Technical terms, project names, or any content

EXAMPLES (remove only START prefix):
- "Dipak Kantilal Callout Update the design" → "Update the design"
- "Sreejith Rajeev Typewriter 166 Update design" → "Update design"
- "Darshna Free Text Please revise" → "Please revise"
- "Mr Manoj Trivedi: Review document" → "Review document"

EXAMPLES (filter out - return "SKIP"):
- "Typewriter 166" → "SKIP" (no comment)
- "Sreejith Rajeev Typewriter 166" → "SKIP" (no comment)
- "B" → "SKIP" (single letter)

EXAMPLES (DO NOT remove names in content):
- "Update as requested by Sreejith" → "Update as requested by Sreejith"
- "Contact Manoj for details" → "Contact Manoj for details"

Input: "{text}"

Output: 
- If ONLY AutoCAD/Typewriter with NO comment: return "SKIP"
- If has comment: return cleaned text (remove names/Typewriter from START, keep ALL comment content)
```

**System Message**:

```
You are an intelligent text cleaner for PDF comments. 
Remove ALL Typewriter patterns (Typewriter 166, Typewriter NC, ttypewriter, etc.) from START only.
Remove ALL names (Hindu names like Sreejith Rajeev, Dipak Kantilal; Muslim names like Mohammed Al Ammari; 
Indian names, and other name patterns) from START only.
Remove ALL annotation labels (Callout, Text Box, Sticky Notes, Free Text, Note) from START only.

If after removing names/Typewriter patterns there's NO actual comment content (like just 'B', single letters, 
or only Typewriter patterns), return 'SKIP'.

If there's actual comment content, return the cleaned text.
When in doubt about whether it's a comment, KEEP IT.
Remove ONLY from the beginning.
Do NOT remove names or labels that appear within the actual comment content.
```

**Cleaning Function**:

```python
def clean_comment(text: str) -> CleaningResult:
    """Clean a single comment"""
    # Step 1: Check if should be skipped (technical elements)
    if is_technical_drawing_element(text):
        return CleaningResult(
            original_text=text,
            cleaned_text="",
            should_skip=True,
            skip_reason="Technical drawing element"
        )
    
    # Step 2: Apply rule-based cleaning
    rule_cleaned = apply_rule_cleaning(text)
    
    # Step 3: Use OpenAI for intelligent cleaning
    if openai_client:
        response = openai_client.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": SYSTEM_MESSAGE},
                {"role": "user", "content": PROMPT}
            ],
            max_tokens=500,
            temperature=0.1
        )
        
        result = response.choices[0].message.content.strip()
        
        if result.upper() == "SKIP":
            return CleaningResult(
                original_text=text,
                cleaned_text="",
                should_skip=True,
                skip_reason="OpenAI determined as technical element"
            )
        
        return CleaningResult(
            original_text=text,
            cleaned_text=result,
            should_skip=False,
            cleaning_method="openai"
        )
    
    return CleaningResult(
        original_text=text,
        cleaned_text=rule_cleaned,
        should_skip=False,
        cleaning_method="rule-based"
    )
```

**Rule-Based Cleaning**:

```python
def apply_rule_cleaning(text: str) -> str:
    """Apply regex-based cleaning rules"""
    cleaned = text.strip()
    
    # Remove annotation type prefixes with names
    # Pattern: "FirstName LastName AnnotationType number"
    for annotation in ["Callout", "Free Text", "Note", "Text Box", "Sticky Notes"]:
        pattern = rf"^([A-Z][a-z]+\s+[A-Z][a-z]+\s+)?{annotation}\s*\d*\s+"
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    
    # Remove name prefixes at start
    name_pattern = r"^([A-Z][a-z]+\s+[A-Z][a-z]+(\s+[A-Z][a-z]+)?)\s+"
    match = re.match(name_pattern, cleaned)
    if match:
        potential_name = match.group(1)
        # Check if recognized name, then remove
        if is_recognized_name(potential_name):
            cleaned = cleaned[len(match.group(0)):].strip()
    
    # Remove title prefixes: Mr, Mrs, Dr, etc.
    cleaned = re.sub(r"^(Mr|Mrs|Ms|Dr|Prof)\s+", "", cleaned, flags=re.IGNORECASE)
    
    # Remove Typewriter patterns
    cleaned = re.sub(r"^Typewriter\s*\d+\s+", "", cleaned, flags=re.IGNORECASE)
    
    # Clean whitespace
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    return cleaned
```

---

### 3. Configuration (crs_config.json)

```json
{
  "comment_cleaning": {
    "common_names": {
      "hindu": ["Sreejith", "Rajeev", "Dipak", "Krishna", "Manoj", "..."],
      "muslim": ["Ahmed", "Ali", "Mohammed", "Hassan", "..."],
      "indian": ["Patel", "Kumar", "Sharma", "..."],
      "western": ["John", "Smith", "Williams", "..."]
    },
    "skip_patterns": [
      "^Typewriter\\s*\\d+\\s*$",
      "^SHX\\s*Text\\s*$",
      "^AutoCAD.*$",
      "^\\d+[\\.\\-]?\\d*$"
    ],
    "annotation_labels": [
      "Text Box", "Callout", "Free Text", "Note", "Highlight"
    ],
    "openai": {
      "model": "gpt-3.5-turbo",
      "max_tokens": 500,
      "temperature": 0.1,
      "enabled": true
    },
    "min_comment_length": 5,
    "max_comment_length": 2000
  }
}
```

---

### 4. Excel Download Logic (Frontend - CRSDocuments.jsx)

```javascript
downloadPreviewAsExcel(format) {
  // Create workbook using XLSX library
  const wb = XLSX.utils.book_new();
  
  // Define headers
  const headers = [
    'Page', 
    'Reviewer', 
    'Comment', 
    'Type', 
    'Discipline', 
    'Drawing Ref', 
    'Status'
  ];
  
  // Map comment data to rows
  const wsData = [
    headers,
    ...previewData.comments.map(comment => [
      comment.page_number,
      comment.reviewer_name,
      comment.comment_text,
      // Type normalization: Only "HOLD" or "General"
      comment.type.toUpperCase() === 'HOLD' ? 'HOLD' : 'General',
      comment.discipline || 'N/A',
      comment.drawing_reference || 'N/A',
      comment.status || 'Open'
    ])
  ];
  
  // Create worksheet
  const ws = XLSX.utils.aoa_to_sheet(wsData);
  
  // Auto-size columns
  ws['!cols'] = [
    {wch: 8},   // Page
    {wch: 20},  // Reviewer
    {wch: 50},  // Comment
    {wch: 12},  // Type
    {wch: 15},  // Discipline
    {wch: 20},  // Drawing Ref
    {wch: 10}   // Status
  ];
  
  // Add worksheet to workbook
  XLSX.utils.book_append_sheet(wb, ws, 'CRS Preview');
  
  // Generate filename with timestamp
  const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
  const filename = `CRS_Preview_${timestamp}.${format}`;
  
  // Download file (format: xlsx or csv)
  XLSX.writeFile(wb, filename);
}
```

**PDF Download Logic**:

```javascript
downloadPreviewAsPDF() {
  // Create PDF with landscape orientation
  const pdf = new jsPDF({
    orientation: 'landscape',
    format: 'a4'
  });
  
  // Define headers
  const headers = [
    'Page', 'Reviewer', 'Comment', 'Type', 
    'Discipline', 'Drawing Ref', 'Status'
  ];
  
  // Map data
  const body = previewData.comments.map(comment => [
    comment.page_number,
    comment.reviewer_name,
    comment.comment_text,
    comment.type.toUpperCase() === 'HOLD' ? 'HOLD' : 'General',
    comment.discipline || 'N/A',
    comment.drawing_reference || 'N/A',
    comment.status || 'Open'
  ]);
  
  // Generate table with jspdf-autotable
  pdf.autoTable({
    head: [headers],
    body: body,
    styles: {
      fontSize: 8,
      cellPadding: 2
    },
    headStyles: {
      fillColor: [66, 139, 202],  // Blue header
      fontStyle: 'bold'
    },
    alternateRowStyles: {
      fillColor: [240, 240, 240]  // Light gray striped rows
    },
    margin: { top: 10 }
  });
  
  // Download PDF
  const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, '-');
  pdf.save(`CRS_Preview_${timestamp}.pdf`);
}
```

---

## 🔧 Backend API Flow

### Upload & Process Endpoint

```python
# views.py - CRS Document Upload
@action(detail=False, methods=['post'])
def upload_and_process(self, request):
    """Upload PDF and extract comments"""
    file = request.FILES.get('file')
    preview = request.data.get('preview', 'false').lower() == 'true'
    
    # Read PDF
    pdf_buffer = BytesIO(file.read())
    
    # Extract comments with cleaning
    comments = extract_reviewer_comments(pdf_buffer, apply_cleaning=True)
    
    # Filter out "Not Provided" reviewers
    filtered_comments = [
        comment for comment in comments 
        if comment.reviewer_name and 
        comment.reviewer_name.lower() != "not provided"
    ]
    
    # Calculate statistics
    stats = calculate_statistics(filtered_comments)
    filtered_stats = stats.copy()
    filtered_stats["total"] = len(filtered_comments)
    
    # Return response
    return Response({
        'success': True,
        'preview': preview,
        'comments': [serialize_comment(c) for c in filtered_comments],
        'statistics': filtered_stats,
        'metadata': {
            'total_extracted': len(comments),
            'filtered_count': len(filtered_comments)
        }
    })
```

**Statistics Calculation**:

```python
def calculate_statistics(comments):
    """Calculate comment statistics"""
    stats = {
        "total": len(comments),
        "by_type": {},
        "by_reviewer": {},
        "by_discipline": {}
    }
    
    for comment in comments:
        # Group by type (normalize to General/HOLD)
        type_key = "HOLD" if comment.comment_type == "RED_COMMENT" else "General"
        stats["by_type"][type_key] = stats["by_type"].get(type_key, 0) + 1
        
        # Group by reviewer
        reviewer = comment.reviewer_name
        stats["by_reviewer"][reviewer] = stats["by_reviewer"].get(reviewer, 0) + 1
        
        # Group by discipline
        discipline = comment.discipline
        stats["by_discipline"][discipline] = stats["by_discipline"].get(discipline, 0) + 1
    
    return stats
```

---

## 📊 Data Flow Diagram

```
PDF Upload
    ↓
PyMuPDF Extraction
    ├── Annotations (popup_text, content, title)
    ├── Color Detection (Yellow/Red)
    └── Text Spans (Red colored text)
    ↓
Technical Element Filter
    ├── AutoCAD/SHX patterns
    ├── Pure numbers/dimensions
    └── Elevation codes
    ↓
Comment Cleaning (OpenAI)
    ├── Remove names from START
    ├── Remove Typewriter patterns
    ├── Remove annotation labels
    └── Return "SKIP" if no content
    ↓
Reviewer Filter
    └── Exclude "Not Provided" reviewers
    ↓
Statistics Calculation
    ├── Total count
    ├── By type (General/HOLD)
    ├── By reviewer
    └── By discipline
    ↓
Frontend Display
    ├── Preview Table
    ├── Statistics Summary
    └── Download Buttons (Excel, CSV, PDF, JSON)
```

---

## 🎯 Key Features

### 1. Intelligent Name Recognition
- Recognizes Hindu, Muslim, Indian, and Western names
- Removes names ONLY from start of comment
- Preserves names in middle/end of comment content

### 2. Multi-line Comment Handling
- max_tokens: 500 supports long comments with bullet lists
- Preserves complete comment structure
- Handles 3-5 bullet point lists

### 3. Type Normalization
- Backend: YELLOW_BOX, RED_COMMENT, ANNOTATION
- Frontend: Normalized to "General" or "HOLD" only
- Consistent across preview table, downloads, and statistics

### 4. Deduplication Control
- Deduplication DISABLED (line 433 commented)
- Preserves all comments including duplicates
- Multiple comments per page are valid

### 5. Download Formats
- Excel (.xlsx): Full data with auto-sized columns
- CSV (.csv): Comma-separated for spreadsheet import
- PDF: Landscape A4 with blue headers and striped rows
- JSON: Raw data for programmatic access

---

## 🔑 Environment Variables

```bash
# Required for OpenAI comment cleaning
OPENAI_API_KEY=sk-...your-key...

# Optional: Disable OpenAI and use rule-based only
OPENAI_ENABLED=false
```

---

## 📝 Usage Example

```python
# Backend Python
from comment_extractor import extract_reviewer_comments
from comment_cleaner import get_comment_cleaner

# Extract comments with cleaning
with open('document.pdf', 'rb') as f:
    pdf_buffer = BytesIO(f.read())
    comments = extract_reviewer_comments(pdf_buffer, apply_cleaning=True)

# Filter and process
filtered = [c for c in comments if c.reviewer_name != "Not Provided"]

# Calculate stats
stats = {
    "total": len(filtered),
    "by_type": {"General": 0, "HOLD": 0}
}
for comment in filtered:
    type_key = "HOLD" if comment.comment_type == "RED_COMMENT" else "General"
    stats["by_type"][type_key] += 1
```

```javascript
// Frontend JavaScript
// Upload and preview
const formData = new FormData();
formData.append('file', pdfFile);
formData.append('preview', 'true');

const response = await axios.post('/api/v1/crs-documents/upload-and-process/', formData);
const { comments, statistics } = response.data;

// Download as Excel
downloadPreviewAsExcel('xlsx');

// Download as PDF
downloadPreviewAsPDF();
```

---

## 🚀 Performance Notes

- **max_tokens: 500** - Handles most comments (~375 words)
- **Deduplication: OFF** - Preserves all comments
- **OpenAI fallback** - Rule-based cleaning if API fails
- **Container caching** - Use `--no-cache` for fresh builds

---

## 📌 Important Constants

```python
# Color thresholds
YELLOW_THRESHOLD = 0.6
RED_THRESHOLD = 0.5

# Comment length limits
MIN_COMMENT_LENGTH = 5
MAX_COMMENT_LENGTH = 2000

# OpenAI settings
MODEL = "gpt-3.5-turbo"
MAX_TOKENS = 500
TEMPERATURE = 0.1
```

---

**End of Documentation**

This file contains all extraction, cleaning, and processing logic for the CRS system.
No git dependencies - completely standalone reference.
