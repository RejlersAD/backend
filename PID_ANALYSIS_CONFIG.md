# P&ID Analysis Configuration Guide

## Overview
The P&ID analysis service uses soft-coded configuration parameters that can be customized via environment variables without code changes.

## Environment Variables

### Analysis Quality Parameters

#### `PID_MIN_ISSUES` (Default: 15)
**Description:** Minimum number of issues and observations the AI must identify per drawing.

**Purpose:** Ensures comprehensive, thorough analysis rather than superficial review.

**Values:**
- `15` (Recommended for production) - Comprehensive analysis with balanced issue distribution
- `10` - Standard analysis for simpler drawings
- `20` - Extra thorough for critical/complex drawings
- `5` - Quick review mode (not recommended for production)

**Usage:**
```bash
# Railway Dashboard → Variables
PID_MIN_ISSUES=15
```

**Impact:** 
- AI will systematically check ALL drawing elements
- Forces examination of equipment datasheets, instrument schedules, line lists, safety devices, etc.
- Results in balanced issue distribution: Critical (2-5), Major (3-7), Minor (3-5), Observations (2-5)

**Note:** See `PID_STRICT_MIN_ISSUES` to control enforcement behavior.

---

#### `PID_STRICT_MIN_ISSUES` (Default: False) **NEW**
**Description:** Controls whether minimum issues requirement is strictly enforced or flexible.

**Purpose:** Allows dynamic issue detection - accepts whatever the AI finds, even if below minimum.

**Values:**
- `False` (Default) - **FLEXIBLE MODE**: Accept any number of issues found by AI
  - AI tries to find 15+ issues but doesn't fail if it finds fewer
  - Better for simple drawings with genuinely few issues
  - Prevents false positives/padding
- `True` - **STRICT MODE**: Enforce minimum issues requirement
  - AI MUST find at least `PID_MIN_ISSUES` issues or warning is logged
  - Better for complex drawings that should have many findings

**Usage:**
```bash
# Railway Dashboard → Variables
PID_STRICT_MIN_ISSUES=False   # Dynamic/flexible mode (recommended)
PID_STRICT_MIN_ISSUES=True    # Strict enforcement
```

**Impact:**
- **Flexible mode** (False): AI extracts as many real issues as possible, no artificial padding
- **Strict mode** (True): Logs warnings if minimum not met, may push AI to find more issues

**Recommended:** Keep as `False` for production to avoid forcing AI to create non-existent issues.

---

#### `PID_MAX_TOKENS` (Default: 16000)
**Description:** Maximum tokens allocated for AI response generation.

**Purpose:** Controls response length and detail level.

**Values:**
- `16000` (Recommended) - Allows comprehensive JSON response with detailed issues
- `12000` - Moderate detail level
- `20000` - Maximum detail (higher cost)
- `8000` - Reduced detail (may truncate observations)

**Usage:**
```bash
PID_MAX_TOKENS=16000
```

**Impact:**
- Higher values = more detailed engineering explanations
- Lower values = may result in incomplete issue descriptions
- Affects OpenAI API cost (more tokens = higher cost)

---

#### `PID_AI_TEMPERATURE` (Default: 0.15)
**Description:** AI creativity/randomness level (0.0 to 2.0).

**Purpose:** Controls consistency and precision of analysis.

**Values:**
- `0.15` (Recommended) - Highly consistent, precise engineering analysis
- `0.0` - Maximum determinism (same drawing → same results)
- `0.3` - Slightly more creative interpretations
- `0.5+` - Not recommended for engineering analysis (too random)

**Usage:**
```bash
PID_AI_TEMPERATURE=0.15
```

**Impact:**
- Lower values = more consistent, repeatable results
- Higher values = more varied interpretations (not ideal for standards compliance)
- Engineering analysis requires precision, so keep below 0.3

---

#### `PID_ANALYSIS_DEPTH` (Default: 'comprehensive')
**Description:** Analysis depth mode (future enhancement placeholder).

**Purpose:** Allow different levels of analysis thoroughness.

**Values:**
- `comprehensive` - Full 7-category verification (current behavior)
- `standard` - Moderate verification (future)
- `quick` - Fast review mode (future)

**Usage:**
```bash
PID_ANALYSIS_DEPTH=comprehensive
```

**Impact:** Currently informational only (future feature).

---

## Railway Configuration

### How to Set Variables in Railway

1. **Go to Railway Dashboard**
   ```
   https://railway.app/project/your-project-id
   ```

2. **Select Backend Service**
   - Click on your backend service (aiflow_backend)

3. **Open Variables Tab**
   - Click "Variables" tab

4. **Add Variables**
   
   | Variable Name | Recommended Value | Description |
   |---------------|-------------------|-------------|
   | `PID_MIN_ISSUES` | `15` | Minimum issues per drawing |
   | `PID_MAX_TOKENS` | `16000` | Max response tokens |
   | `PID_AI_TEMPERATURE` | `0.15` | AI precision level |

5. **Deploy**
   - Railway auto-redeploys after variable changes

---

## Recommended Configurations

### Production Environment (Comprehensive Analysis)
```bash
PID_MIN_ISSUES=15
PID_MAX_TOKENS=16000
PID_AI_TEMPERATURE=0.15
PID_ANALYSIS_DEPTH=comprehensive
```

**Best for:** Final design reviews, client deliverables, safety-critical systems

---

### Development Environment (Standard Analysis)
```bash
PID_MIN_ISSUES=10
PID_MAX_TOKENS=12000
PID_AI_TEMPERATURE=0.2
PID_ANALYSIS_DEPTH=standard
```

**Best for:** Testing, iterative design reviews, non-critical systems

---

### Quick Review Mode (Not Recommended for Production)
```bash
PID_MIN_ISSUES=5
PID_MAX_TOKENS=8000
PID_AI_TEMPERATURE=0.3
PID_ANALYSIS_DEPTH=quick
```

**Best for:** Initial concept reviews, very simple drawings

---

## Impact on Analysis Results

### With PID_MIN_ISSUES=15 (Recommended):

**Expected Issue Distribution:**
- ✅ Critical (Safety): 2-5 issues
- ✅ Major (Operational): 3-7 issues
- ✅ Minor (Documentation): 3-5 issues
- ✅ Observations (Improvements): 2-5 suggestions

**Verification Coverage:**
- ✅ Equipment datasheets (pressures, temperatures, materials)
- ✅ Instrument schedules (ranges, alarms, fail-safe positions)
- ✅ Line lists (sizing, specifications, spec breaks)
- ✅ Safety devices (PSVs, ESD valves, fire & gas detection)
- ✅ Control loops (bypass valves, fail-actions, redundancy)
- ✅ Utility connections (cooling water, instrument air, steam)
- ✅ Isolation valves (maintenance access, locked positions)
- ✅ Piping routing (dead legs, drainage, thermal expansion)
- ✅ Documentation (legends, notes, cross-references)

**Example Real Issues Found:**
1. "PSV-101 set pressure (48 barg) exceeds vessel V-101 MAWP (45 barg) by 6.7%" (Critical)
2. "Control valve FCV-201 shows FC (Fail-Close) for cooling water service - should be FO (Fail-Open)" (Critical)
3. "Line 6\"-P-1501 changes from CS-150 to SS316-300 without spec break marking" (Major)
4. "Instrument LT-301 schedule missing alarm setpoints (HH, H, L, LL)" (Minor)
5. "Consider adding redundant level transmitter for critical vessel V-101" (Observation)

---

### With PID_MIN_ISSUES=5 (Not Recommended):

**Expected Issue Distribution:**
- ⚠️ Critical: 1-2 issues (may miss safety issues)
- ⚠️ Major: 1-2 issues (superficial review)
- ⚠️ Minor: 1-2 issues (many documentation gaps missed)
- ⚠️ Observations: 0-1 suggestions (no optimization opportunities)

**Verification Coverage:**
- ⚠️ Only obvious, glaring errors detected
- ⚠️ Subtle compliance issues missed
- ⚠️ Documentation completeness not checked
- ⚠️ No operational optimization suggestions

---

## Cost Considerations

### OpenAI API Cost Formula:
```
Cost per drawing ≈ (Input tokens × Input rate) + (Output tokens × Output rate)
```

**Typical Token Usage:**
- Input tokens: ~3,000 - 8,000 (depending on drawing complexity + RAG context)
- Output tokens: ~5,000 - 15,000 (with PID_MAX_TOKENS=16000)

**Cost Impact:**
- `PID_MAX_TOKENS=8000`: ~$0.10 - $0.20 per drawing
- `PID_MAX_TOKENS=16000`: ~$0.15 - $0.30 per drawing
- `PID_MAX_TOKENS=20000`: ~$0.20 - $0.40 per drawing

**Recommendation:** Use PID_MAX_TOKENS=16000 for production (best quality-cost balance)

---

## Monitoring & Validation

### Check Railway Logs
```bash
[CONFIG] Analysis parameters: MIN_ISSUES=15, MAX_TOKENS=16000, TEMPERATURE=0.15
[INFO] Tokens used: 14523
```

### Verify Configuration Active
Access diagnostic endpoint:
```
GET https://aiflowbackend-production.up.railway.app/api/v1/pid/drawings/{id}/
```

Check response for total_issues count - should be ≥ MIN_ISSUES setting.

---

## Troubleshooting

### Issue: "Drawing analysis returns < 15 issues"

**Solutions:**
1. Check Railway environment variables are set correctly
2. Verify PID_MIN_ISSUES=15 in Variables tab
3. Set PID_STRICT_MIN_ISSUES=False for flexible/dynamic mode
4. Redeploy service after adding variables
5. Check Railway logs for `[CONFIG] Analysis parameters` line

**Note:** With `PID_STRICT_MIN_ISSUES=False` (default), the system accepts any number of issues the AI finds. This is RECOMMENDED to avoid forcing AI to create non-existent issues.

---

### Issue: "Analysis failed: Malformed AI response with invalid key"

**Error Message:** 
```
Analysis failed: Analysis failed: Malformed AI response with invalid key '\n "drawing_info"'
```

**Cause:** OpenAI sometimes returns JSON with whitespace/newlines in keys (e.g., `"\n \"drawing_info\""` instead of `"drawing_info"`).

**Solutions:**
✅ **Automatic Recovery (v2.0+):** The system now includes aggressive JSON sanitization:
1. All whitespace characters (newlines, tabs, spaces) are stripped from keys
2. All quote characters are removed from keys
3. Multi-pass cleaning catches nested patterns
4. Recovery mechanism attempts re-sanitization on KeyError
5. Fallback returns minimal valid structure

**What happens now:**
- AI response is automatically sanitized BEFORE processing
- Malformed keys like `'\n "drawing_info"'` → `'drawing_info'`
- If sanitization fails, system tries recovery
- If recovery fails, returns partial results instead of total failure

**Monitor logs for:**
```
[WARNING] Skipping empty key after sanitization: ...
[RECOVERY] Attempting aggressive re-sanitization...
[RECOVERY] Re-sanitized keys: ...
```

**If error persists:**
1. Check Railway logs for full AI response
2. May indicate API rate limiting or quota issues
3. Try re-uploading the drawing
4. Contact support with error message

---

### Issue: "Analysis truncated / incomplete JSON"

**Solutions:**
1. Increase PID_MAX_TOKENS to 20000
2. Check if drawing is exceptionally complex (many pages)
3. Review Railway logs for token usage warnings

---

### Issue: "Results inconsistent between runs"

**Solutions:**
1. Lower PID_AI_TEMPERATURE to 0.10 or 0.05
2. Check for PID_AI_TEMPERATURE > 0.3 (too high)
3. Use temperature 0.0 for maximum consistency

---

## Default Behavior (No Environment Variables)

If environment variables are NOT set, the system uses these defaults:
```python
PID_MIN_ISSUES = 15
PID_MAX_TOKENS = 16000
PID_AI_TEMPERATURE = 0.15
PID_ANALYSIS_DEPTH = 'comprehensive'
```

This ensures production-quality analysis even without explicit configuration.

---

## Summary

**Recommended Production Setup:**
```bash
# Railway Dashboard → Backend Service → Variables
PID_MIN_ISSUES=15
PID_MAX_TOKENS=16000
PID_AI_TEMPERATURE=0.15
```

**Result:** Comprehensive, consistent, engineering-grade P&ID analysis with minimum 15 actionable issues per drawing.
