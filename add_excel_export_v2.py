"""
Add Excel export and Download button to Transformer Verification
Uses insertAfter pattern matching for precise placement
"""

frontend_file = r"C:\Users\Abdullah.Khan\airflow_frontend\src\pages\Engineering\Electrical\ElectricalEquipmentDatasheet.jsx"

with open(frontend_file, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Add XLSX import if not already present
if "import * as XLSX from 'xlsx';" not in content:
    # Find the HeroIcons import line
    heroicons_import = "} from '@heroicons/react/24/outline';"
    if heroicons_import in content:
        content = content.replace(
            heroicons_import,
            heroicons_import + "\nimport * as XLSX from 'xlsx';"
        )
        print("Added xlsx import")
    else:
        print("WARNING: Could not find HeroIcons import")
else:
    print("xlsx import already present")

# 2. Add ArrowDownTrayIcon to HeroIcons imports if not present
if "ArrowDownTrayIcon" not in content:
    # Find the imports from heroicons
    imports_line = "  ExclamationTriangleIcon,"
    if imports_line in content:
        content = content.replace(
            imports_line,
            imports_line + "\n  ArrowDownTrayIcon,"
        )
        print("Added ArrowDownTrayIcon import")
    else:
        print("WARNING: Could not add ArrowDownTrayIcon")
else:
    print("ArrowDownTrayIcon already imported")

# 3. Add Excel export function after handleVerifyTransformer
export_function = """
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

      console.log(`Excel file exported: ${filename}`);
    } catch (error) {
      console.error('Error exporting to Excel:', error);
      alert('Failed to export Excel file. Please try again.');
    }
  };
"""

if "handleExportToExcel" not in content:
    # Find a good anchor point - after handleVerifyTransformer closing brace
    anchor = "  };\n\n  // Reset transformer verification"
    if anchor in content:
        content = content.replace(anchor, export_function + "\n" + anchor)
        print("Added Excel export function")
    else:
        print("WARNING: Could not find function insertion point")
else:
    print("handleExportToExcel already exists")

# 4. Add Download Excel button after the results table
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

if "Download Excel" not in content:
    # Find the end of the table and add button before reset button
    table_anchor = "                  </tbody>\n                </table>\n              </div>\n            </div>\n\n            {/* Reset Button */}"
    if table_anchor in content:
        content = content.replace(
            table_anchor,
            "                  </tbody>\n                </table>\n              </div>\n            </div>\n" + download_button + "\n            {/* Reset Button */}"
        )
        print("Added Download Excel button")
    else:
        print("WARNING: Could not find table end for button placement")
else:
    print("Download Excel button already exists")

# Write back
with open(frontend_file, 'w', encoding='utf-8') as f:
    f.write(content)

print("\nCompleted! xlsx package installed, frontend updated.")
print("Restart frontend to see changes.")
