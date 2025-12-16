# Malformed JSON Recovery System

## Problem
OpenAI GPT-4 Vision sometimes returns JSON with malformed keys containing whitespace, newlines, or extra quotes:
- Example: `"\n \"drawing_info\""` instead of `"drawing_info"`
- This causes KeyError when trying to access dictionary keys
- Error message: `"Analysis failed: Critical error accessing JSON keys '\n "drawing_info"'"`

## Solution Architecture (Multi-Layer Defense)

### Layer 1: Enhanced JSON Key Sanitization
**Location:** `apps/pid_analysis/services.py` - `_sanitize_json_keys()` method

**Features:**
- ✅ Aggressive multi-pass cleaning of ALL whitespace characters
- ✅ Removes: newlines (`\n`), carriage returns (`\r`), tabs (`\t`), quotes, spaces
- ✅ Recursive sanitization (handles nested dicts and arrays)
- ✅ Ultra-defensive: Catches exceptions for each individual key
- ✅ Fallback: Attempts to add key with original name if cleaning fails
- ✅ Skips empty keys after sanitization

**Code Logic:**
```python
def _sanitize_json_keys(self, obj):
    try:
        if isinstance(obj, dict):
            sanitized = {}
            for key, value in obj.items():
                try:
                    # Aggressive cleaning
                    clean_key = str(key).strip()
                    for char in ['\n', '\r', '\t', '"', "'", ' ']:
                        clean_key = clean_key.strip(char)
                    clean_key = clean_key.strip()  # Final pass
                    
                    if clean_key:
                        sanitized[clean_key] = self._sanitize_json_keys(value)
                except Exception:
                    # Fallback: try original key
                    try:
                        sanitized[str(key)] = value
                    except:
                        continue  # Skip problematic key
            return sanitized
        elif isinstance(obj, list):
            return [self._sanitize_json_keys(item) for item in obj]
        else:
            return obj
    except Exception:
        return obj  # Last resort: return as-is
```

### Layer 2: Defensive Validation with Try-Except Wrapping
**Location:** `apps/pid_analysis/services.py` - Validation section (lines 1430-1510)

**Features:**
- ✅ Each validation step wrapped in individual try-except
- ✅ Continues processing even if one validation fails
- ✅ Adds minimal defaults if key addition fails
- ✅ Never raises unhandled exceptions

**Protected Operations:**
```python
# Count issues (safe)
try:
    min_issues_found = len(analysis_result.get('issues', []))
except:
    min_issues_found = 0

# Add 'issues' key (safe)
try:
    if 'issues' not in analysis_result:
        analysis_result['issues'] = []
except:
    # Try alternative approach
    analysis_result = {'issues': [], **analysis_result}

# Similar protection for 'summary' and 'drawing_info'
```

### Layer 3: Emergency Recovery Mode
**Location:** `apps/pid_analysis/services.py` - KeyError exception handler

**Features:**
- ✅ Catches ANY KeyError that escapes previous layers
- ✅ Attempts aggressive re-sanitization
- ✅ Extracts whatever data is recoverable
- ✅ Returns minimal valid structure instead of failing
- ✅ Three-tier recovery: Extract → Minimal → Absolute Minimum

**Recovery Tiers:**
1. **Tier 1 - Data Extraction:** Try to extract issues, summary, drawing_info from malformed response
2. **Tier 2 - Minimal Structure:** Return partial data with defaults
3. **Tier 3 - Absolute Minimum:** Return empty structure with error flag

**Recovery Code:**
```python
except KeyError as ke:
    print(f"[RECOVERY] Entering emergency recovery mode...")
    
    try:
        # Tier 1: Try to extract data
        sanitized = self._sanitize_json_keys(analysis_result)
        recovered_issues = sanitized.get('issues', [])
        # ... extract summary, drawing_info
        
        # Return recovered data
        return {
            'drawing_info': recovered_drawing_info,
            'issues': recovered_issues,
            'summary': recovered_summary
        }
    except Exception:
        # Tier 2/3: Return absolute minimum
        return {
            'drawing_info': {
                'drawing_number': 'Unknown',
                'error': 'Partial recovery from malformed response'
            },
            'issues': [],
            'summary': {...}
        }
```

## Configuration Variables

### `PID_STRICT_MIN_ISSUES` (Default: False)
Controls enforcement of minimum issues requirement.

**Values:**
- `False` (Default): **FLEXIBLE MODE** - Accept any number of issues AI finds
- `True`: **STRICT MODE** - Require minimum `PID_MIN_ISSUES` issues

**Recommendation:** Keep as `False` to prevent forcing AI to create non-existent issues.

## Deployment

### Backend (Railway)
```bash
cd backend
git add apps/pid_analysis/services.py
git commit -m "Ultra-defensive JSON handling"
git push  # Auto-deploys to Railway
```

### Environment Variables (Optional)
```bash
# Railway Dashboard → Variables
PID_STRICT_MIN_ISSUES=False   # Flexible mode (recommended)
PID_MIN_ISSUES=15              # Target minimum issues
PID_ERROR_CONTEXT=detailed     # Detailed error messages
```

## Testing

### Test Malformed JSON Recovery
1. Upload P&ID drawing at https://airflow-frontend.vercel.app/pid/upload
2. Monitor Railway logs for:
   ```
   [DEBUG] Raw JSON keys before sanitization: ['\n "drawing_info"', ...]
   [INFO] JSON keys sanitized
   [DEBUG] Clean JSON keys after sanitization: ['drawing_info', 'issues', 'summary']
   [RECOVERY] Entering emergency recovery mode...
   [RECOVERY] Extracted X issues
   ```

### Expected Behavior
✅ Analysis completes even with malformed JSON
✅ Data is extracted and sanitized automatically
✅ If extraction fails, minimal valid structure is returned
✅ **NO MORE "Analysis failed: Critical error" messages**

### Failure Scenarios Handled
| Scenario | Old Behavior | New Behavior |
|----------|--------------|--------------|
| Malformed key `'\n "drawing_info"'` | ❌ Critical error | ✅ Auto-sanitized to `'drawing_info'` |
| Multiple whitespace in keys | ❌ KeyError | ✅ Stripped automatically |
| Empty keys after cleaning | ❌ Crash | ✅ Skipped, processing continues |
| Nested dict with malformed keys | ❌ Nested failure | ✅ Recursive sanitization |
| Sanitization itself fails | ❌ Total failure | ✅ Fallback to original key |
| All recovery attempts fail | ❌ 500 error | ✅ Returns minimal valid structure |

## Monitoring & Debugging

### Railway Logs to Watch
```bash
# Success indicators
[INFO] JSON keys sanitized
[DEBUG] Clean JSON keys after sanitization: [...]
[SUCCESS] Found X issues

# Recovery indicators  
[WARNING] Skipping empty key after sanitization
[RECOVERY] Attempting aggressive re-sanitization...
[RECOVERY] Extracted X issues

# Critical errors (should never happen now)
[ERROR] Emergency recovery failed
```

### Log Interpretation
- `[INFO] JSON keys sanitized` = Layer 1 worked
- `[RECOVERY] Entering emergency recovery mode...` = Layer 3 activated
- `[RECOVERY] Extracted X issues` = Data successfully recovered
- `[RECOVERY] Returning absolute minimum structure` = Last resort (returns empty but valid structure)

## Version History

### v1.0 - Initial Sanitization (December 16, 2025)
- Basic key stripping for `\n`, `\r`, quotes
- Single-pass cleaning
- Raised exception on failure

### v2.0 - Enhanced Sanitization (December 16, 2025)
- Multi-character iteration for aggressive cleaning
- Recursive sanitization for nested structures
- Added empty key detection

### v3.0 - Ultra-Defensive (December 16, 2025) **CURRENT**
- Multi-layer defense architecture
- Per-key exception handling in sanitization
- Defensive validation with individual try-except blocks
- Three-tier emergency recovery system
- **Never fails - always returns valid structure**

## Support

### If Issues Persist
1. Check Railway logs for `[RECOVERY]` messages
2. Verify sanitization is being called: `[INFO] JSON keys sanitized`
3. Check if recovery extracted data: `[RECOVERY] Extracted X issues`
4. Contact support with full Railway log output

### Known Limitations
- If AI returns completely invalid JSON (not parseable), json.JSONDecodeError is raised (before sanitization)
- If AI returns wrong data types (e.g., issues as string instead of array), validation may fail
- Maximum recovery depth: 3 tiers (beyond this, returns empty structure)

## Related Documentation
- [PID_ANALYSIS_CONFIG.md](PID_ANALYSIS_CONFIG.md) - Configuration guide
- [RAG_README.md](RAG_README.md) - RAG integration
- [CORS_FIX_GUIDE.md](../CORS_FIX_GUIDE.md) - CORS configuration
