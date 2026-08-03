"""
Add Excel Export to Transformer Verification Results
Adds download button and xlsx export functionality
"""

import re

frontend_file = r"C:\Users\Abdullah.Khan\airflow_frontend\src\pages\Engineering\Electrical\ElectricalEquipmentDatasheet.jsx"

# Read the file
with open(frontend_file, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add xlsx import at the top (after other imports)
import_section = """import React, { useState, useEffect } from 'react';
import { useNavigate } from 'router-dom';
import axios from 'axios';
import { 
  DocumentTextIcon, 
  CloudArrowUpIcon, 
  CheckCircleIcon,
  XCircleIcon,
  ExclamationTriangleIcon,
  QuestionMarkCircleIcon,
  ArrowDownTrayIcon
} from '@heroicons/react/24/outline';
import * as XLSX from 'xlsx';"""

# Find the existing import section and replace
existing_imports = re.search(
    r"import React.*?from '@heroicons/react/24/outline';",
    content,
    re.DOTALL
)

if existing_imports:
    content = content.replace(
        existing_imports.group(0),
        import_section
    )
    print("✅ Added xlsx import")
else:
    print("⚠️ Could not find import section - imports may need manual addition")

# 2. Add Excel export function before the return statement
# Find the handleVerifyTransformer function end
excel_export_function = """
  // Export verification results to Excel
  const handleExportToExcel = () => {
    if (!verificationResults || verificationResults.length === 0) {
      alert('No verification results to export');
      return;
    }

    try {
      // Prepare data for Excel (same structure as HTML table)
      const excelData = verificationResults.map(result => ({
        'Parameter': result.parameter || '',
        'Datasheet Value': result.datasheet_value || '',
        'Document Value': result.document_value || result.calculated_value || '',
        'Status': result.status || '',
        'Explanation': result.explanation || '',
        'Confidence': result.confidence || ''
      }));

      // Create worksheet
      const ws = XLSX.utils.json_to_sheet(excelData);

      // Set column widths for better readability
      ws['!cols'] = [
        { wch: 25 },  // Parameter
        { wch: 20 },  // Datasheet Value
        { wch: 20 },  // Document Value
        { wch: 12 },  // Status
        { wch: 60 },  // Explanation (wider for detailed text)
        { wch: 12 }   // Confidence
      ];

      // Create workbook
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, 'Verification Results');

      // Generate filename with timestamp
      const timestamp = new Date().toISOString().split('T')[0];
      const filename = `Transformer_Datasheet_Verification_${timestamp}.xlsx`;

      // Download file
      XLSX.writeFile(wb, filename);

      console.log(`✅ Excel file exported: ${filename}`);
    } catch (error) {
      console.error('Error exporting to Excel:', error);
      alert('Failed to export Excel file. Please try again.');
    }
  };
"""

# Find where to insert (after handleVerifyTransformer function)
verify_function_end = content.find('} catch (error) {\n      console.error(\'Verification error:\', error);')
if verify_function_end != -1:
    # Find the end of the catch block
    next_closing = content.find('\n  };', verify_function_end)
    if next_closing != -1:
        insertion_point = next_closing + len('\n  };')
        content = content[:insertion_point] + '\n' + excel_export_function + content[insertion_point:]
        print("✅ Added Excel export function")
    else:
        print("⚠️ Could not find function insertion point")
else:
    print("⚠️ Could not locate handleVerifyTransformer function end")

# 3. Add Download Excel button in the results section
# Find the verification results table
download_button = """
                    {/* Download Excel Button */}
                    <div className="mt-6 flex justify-end">
                      <button
                        onClick={handleExportToExcel}
                        className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-green-600 hover:bg-green-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-green-500"
                      >
                        <ArrowDownTrayIcon className="h-5 w-5 mr-2" />
                        Download Excel
                      </button>
                    </div>
"""

# Find the results table closing div and add button after it
# Look for the end of the verification results table
table_pattern = r'(</tbody>\s*</table>\s*</div>\s*</div>)(\s*{/\* Reset Button \*/})'
match = re.search(table_pattern, content)

if match:
    # Insert download button before Reset Button
    content = content.replace(
        match.group(0),
        match.group(1) + download_button + match.group(2)
    )
    print("✅ Added Download Excel button")
else:
    print("⚠️ Could not find table end - button may need manual placement")

# Write back
with open(frontend_file, 'w', encoding='utf-8') as f:
    f.write(content)

print("\n✅ Excel export functionality added!")
print("📦 Install xlsx package: cd airflow_frontend && npm install xlsx")
print("🔄 Restart frontend: npm run dev")
