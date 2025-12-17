# Report Generation Configuration Guide

## Overview
The AIFlow report generation system now uses **soft-coded configuration** through environment variables, allowing you to customize branding and appearance without modifying code.

## Environment Variables

### Company Branding

```bash
# Company Information
REPORT_COMPANY_NAME="REJLERS ABU DHABI"
REPORT_COMPANY_SUBTITLE="Engineering & Design Consultancy"
REPORT_COMPANY_WEBSITE="www.rejlers.com/ae"
```

### Report Colors

All colors are specified as **6-digit hex codes without the # symbol**.

```bash
# Primary color (used for headers, titles, table headers)
REPORT_PRIMARY_COLOR="003366"  # Dark blue

# Secondary color (for accents and highlights)
REPORT_SECONDARY_COLOR="FFA500"  # Orange

# Text color (main body text)
REPORT_TEXT_COLOR="333333"  # Dark gray

# Border color (table borders, dividers)
REPORT_BORDER_COLOR="CCCCCC"  # Light gray
```

### Report Template Settings

```bash
# Main report title
REPORT_TITLE="P&ID DESIGN VERIFICATION REPORT"

# Footer text (appears at bottom of reports)
REPORT_FOOTER_TEXT="CONFIDENTIAL ENGINEERING DOCUMENT"

# Footer note with company placeholder
# Use {company} as placeholder for company name
REPORT_FOOTER_NOTE="This document is the property of {company}. Unauthorized distribution is prohibited."
```

## Usage Examples

### Example 1: Custom Company Branding

For a different company, create a `.env` file or set environment variables:

```bash
REPORT_COMPANY_NAME="ACME Engineering Ltd"
REPORT_COMPANY_SUBTITLE="Industrial Design & Automation"
REPORT_COMPANY_WEBSITE="www.acme-engineering.com"
REPORT_PRIMARY_COLOR="1E3A8A"  # Different blue
REPORT_SECONDARY_COLOR="F59E0B"  # Amber
```

### Example 2: Different Report Style

```bash
REPORT_TITLE="PIPING & INSTRUMENTATION DIAGRAM ANALYSIS"
REPORT_FOOTER_TEXT="PROPRIETARY AND CONFIDENTIAL"
REPORT_FOOTER_NOTE="Copyright © {company}. All rights reserved."
```

### Example 3: Minimal/Simple Style

```bash
REPORT_COMPANY_NAME="Engineering Services"
REPORT_COMPANY_SUBTITLE=""  # Leave empty for no subtitle
REPORT_COMPANY_WEBSITE="engineering.example.com"
REPORT_PRIMARY_COLOR="000000"  # Black
REPORT_SECONDARY_COLOR="666666"  # Gray
REPORT_FOOTER_TEXT="Internal Use Only"
```

## Configuration in Different Environments

### Local Development (.env file)

Create/edit `backend/.env`:

```bash
# Report Configuration
REPORT_COMPANY_NAME="REJLERS ABU DHABI"
REPORT_PRIMARY_COLOR="003366"
# ... other settings
```

### Railway Deployment

Set environment variables in Railway dashboard:

1. Go to your Railway project
2. Click on Variables
3. Add each variable:
   - `REPORT_COMPANY_NAME` = `REJLERS ABU DHABI`
   - `REPORT_PRIMARY_COLOR` = `003366`
   - etc.

### Docker Deployment

Add to `docker-compose.yml`:

```yaml
services:
  backend:
    environment:
      - REPORT_COMPANY_NAME=REJLERS ABU DHABI
      - REPORT_PRIMARY_COLOR=003366
      - REPORT_TITLE=P&ID DESIGN VERIFICATION REPORT
      # ... other variables
```

## Default Values

If no environment variables are set, the system uses these defaults:

| Variable | Default Value |
|----------|--------------|
| REPORT_COMPANY_NAME | REJLERS ABU DHABI |
| REPORT_COMPANY_SUBTITLE | Engineering & Design Consultancy |
| REPORT_COMPANY_WEBSITE | www.rejlers.com/ae |
| REPORT_PRIMARY_COLOR | 003366 (Dark Blue) |
| REPORT_SECONDARY_COLOR | FFA500 (Orange) |
| REPORT_TEXT_COLOR | 333333 (Dark Gray) |
| REPORT_BORDER_COLOR | CCCCCC (Light Gray) |
| REPORT_TITLE | P&ID DESIGN VERIFICATION REPORT |
| REPORT_FOOTER_TEXT | CONFIDENTIAL ENGINEERING DOCUMENT |
| REPORT_FOOTER_NOTE | This document is the property of {company}... |

## Report Formats Supported

All three export formats use these configurations:

1. **PDF Reports** - Full branding with colors, logos, and formatting
2. **Excel Reports** - Professional spreadsheets with branding
3. **CSV Reports** - Simple text format with company header

## Color Palette Recommendations

### Professional Blue Theme (Default)
- Primary: `003366` (Navy Blue)
- Secondary: `FFA500` (Orange)

### Corporate Gray Theme
- Primary: `2C3E50` (Dark Gray-Blue)
- Secondary: `E74C3C` (Red)

### Modern Tech Theme
- Primary: `6366F1` (Indigo)
- Secondary: `10B981` (Green)

### Energy Industry Theme
- Primary: `065F46` (Dark Green)
- Secondary: `FBBF24` (Yellow)

## Troubleshooting

### Reports show default branding instead of custom
- ✅ Check that environment variables are set correctly
- ✅ Restart the application after changing variables
- ✅ Verify no typos in variable names

### Colors not displaying correctly
- ✅ Ensure hex codes are 6 characters (no #)
- ✅ Use valid hex color codes only (0-9, A-F)

### Footer note not showing company name
- ✅ Use `{company}` placeholder in REPORT_FOOTER_NOTE
- ✅ Check REPORT_COMPANY_NAME is set

## Security Notes

- ⚠️ Keep confidential company information out of version control
- ✅ Use `.env` files for local development
- ✅ Use platform environment variables for production
- ✅ Add `.env` to `.gitignore`

## Testing Changes

After modifying report configuration:

1. Restart the Django application
2. Upload a P&ID drawing
3. Generate a report in PDF/Excel/CSV format
4. Verify branding appears correctly

## Support

For issues or questions about report configuration:
- Check the application logs for configuration warnings
- Verify all environment variables are properly set
- Test with default values first before customizing
