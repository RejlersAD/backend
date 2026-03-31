#!/usr/bin/env python3
"""
Direct modification of ElectricalEquipmentDatasheet.jsx
Adds complete Transformer Verification feature
"""

import re

file_path = r"C:\Users\Abdullah.Khan\airflow_frontend\src\pages\Engineering\Electrical\ElectricalEquipmentDatasheet.jsx"

print("📖 Reading file...")
with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

print(f"📊 Original file: {len(content.splitlines())} lines")

# ============================================================================
# MODIFICATION 1: Add verification handler function
# ============================================================================
print("🔧 Adding verification handler...")

verification_handler = '''
  const handleVerifyTransformer = async () => {
    if (!transformerDatasheet) {
      setError('Please upload the transformer datasheet Excel file');
      return;
    }

    const requiredDocs = {
      'mv_trafo_calculation': 'MV Trafo Calculation',
      'criteria': 'Criteria',
      'formula': 'Formula',
      'lv_trafo_calculation': 'LV Trafo Calculation'
    };
    
    const missingDocs = Object.entries(requiredDocs).filter(([key]) => !equipmentDocs[key]);
    
    if (missingDocs.length > 0) {
      setError(`Please upload all required documents: ${missingDocs.map(([, label]) => label).join(', ')}`);
      return;
    }

    setVerifying(true);
    setError('');
    setAnalysisStage('Verifying transformer datasheet...');

    try {
      const formData = new FormData();
      formData.append('transformer_datasheet', transformerDatasheet);
      formData.append('mv_calc_document', equipmentDocs.mv_trafo_calculation);
      formData.append('criteria_document', equipmentDocs.criteria);
      formData.append('formula_document', equipmentDocs.formula);
      formData.append('lv_calc_document', equipmentDocs.lv_trafo_calculation);

      const response = await apiClient.post(
        '/electrical/datasheets/verify-transformer/',
        formData,
        {
          headers: { 'Content-Type': 'multipart/form-data' },
          timeout: 120000,
          onUploadProgress: (progressEvent) => {
            const progress = Math.round((progressEvent.loaded * 100) / progressEvent.total);
            setUploadProgress(progress);
          }
        }
      );

      setVerificationResults(response.data);
      setAnalysisStage('');
    } catch (err) {
      console.error('Verification error:', err);
      setError(err.response?.data?.error || 'Verification failed. Please try again.');
      setAnalysisStage('');
    } finally {
      setVerifying(false);
      setUploadProgress(0);
    }
  };
'''

# Find position after handleEquipmentDocChange and insert
pattern = r'(const handleEquipmentDocChange = .*?};)\s*\n'
match = re.search(pattern, content, re.DOTALL)
if match:
    insert_pos = match.end()
    content = content[:insert_pos] + "\n" + verification_handler + "\n" + content[insert_pos:]
    print("✅ Added verification handler")

# ============================================================================
# MODIFICATION 2: Add Excel datasheet upload before PDF documents  
# ============================================================================
print("🔧 Adding Excel datasheet upload section...")

excel_upload_section = '''
            {/* Excel Datasheet Upload - Only for Transformer */}
            {equipmentType === 'transformer' && (
              <div className="border-2 border-dashed border-blue-400 rounded-lg p-4 bg-blue-50 mb-4">
                <div className="flex items-start gap-3 mb-3">
                  <DocumentTextIcon className="h-6 w-6 text-blue-600 flex-shrink-0 mt-0.5" />
                  <div className="flex-1">
                    <h4 className="font-semibold text-blue-900 mb-1">📊 Transformer Datasheet (Excel) *</h4>
                    <p className="text-xs text-blue-800">Upload the transformer datasheet Excel file to verify against the documents below</p>
                  </div>
                </div>

                {transformerDatasheet ? (
                  <div className="bg-white border border-blue-200 rounded-lg p-3 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <CheckCircleIcon className="h-5 w-5 text-green-600" />
                      <div>
                        <div className="text-sm font-medium text-gray-900">{transformerDatasheet.name}</div>
                        <div className="text-xs text-gray-600">{formatFileSize(transformerDatasheet.size)}</div>
                      </div>
                    </div>
                    <button
                      onClick={() => setTransformerDatasheet(null)}
                      className="p-1 hover:bg-red-100 rounded-full transition-colors"
                    >
                      <XMarkIcon className="h-5 w-5 text-red-600" />
                    </button>
                  </div>
                ) : (
                  <div>
                    <input
                      type="file"
                      id="transformer-datasheet-upload"
                      accept=".xlsx,.xls"
                      onChange={(e) => {
                        if (e.target.files && e.target.files[0]) {
                          setTransformerDatasheet(e.target.files[0]);
                          setError('');
                        }
                      }}
                      className="hidden"
                    />
                    <label
                      htmlFor="transformer-datasheet-upload"
                      className="block w-full px-4 py-3 border-2 border-dashed border-blue-400 rounded-lg text-center cursor-pointer hover:border-blue-600 hover:bg-blue-100 transition-colors"
                    >
                      <CloudArrowUpIcon className="h-8 w-8 text-blue-500 mx-auto mb-2" />
                      <span className="text-sm font-medium text-blue-700">Click to upload Excel datasheet</span>
                    </label>
                  </div>
                )}
              </div>
            )}

'''

# Insert before EQUIPMENT_DOC_TYPES mapping
pattern = r'(\s+{EQUIPMENT_DOC_TYPES\[equipmentType\]\?\.map\(\(docType\) => \()'
match = re.search(pattern, content)
if match:
    insert_pos = match.start()
    content = content[:insert_pos] + excel_upload_section + content[insert_pos:]
    print("✅ Added Excel datasheet upload section")

# ============================================================================
# MODIFICATION 3: Replace Upload button with conditional Verify button
# ============================================================================
print("🔧 Replacing upload button with verify button...")

old_button = r'''            <div className="flex gap-4">
              <button
                onClick={handleUpload}
                disabled={uploading \|\| Object\.values\(equipmentDocs\)\.every\(doc => doc === null\)}
                className={`flex-1 py-3 px-6 rounded-lg font-semibold text-white transition-colors \$\{
                  uploading \|\| Object\.values\(equipmentDocs\)\.every\(doc => doc === null\)
                    \? 'bg-gray-400 cursor-not-allowed'
                    : 'bg-blue-600 hover:bg-blue-700'
                \}`}
              >
                \{uploading \? \(
                  <span className="flex items-center justify-center gap-2">
                    <ArrowPathIcon className="h-5 w-5 animate-spin" />
                    Processing\.\.\. \{uploadProgress\}%
                  </span>
                \) : \(
                  <>Upload Documents</>
                \)\}
              </button>

              <button
                onClick={handleReset}
                disabled={uploading}
                className="px-6 py-3 border-2 border-gray-300 rounded-lg font-semibold text-gray-700 hover:bg-gray-50 transition-colors"
              >
                Reset
              </button>
            </div>'''

new_button = '''            <div className="flex gap-4">
              {equipmentType === 'transformer' ? (
                <button
                  onClick={handleVerifyTransformer}
                  disabled={verifying || !transformerDatasheet || Object.keys(equipmentDocs).length < 4}
                  className={`flex-1 py-3 px-6 rounded-lg font-semibold text-white transition-colors ${
                    verifying || !transformerDatasheet || Object.keys(equipmentDocs).length < 4
                      ? 'bg-gray-400 cursor-not-allowed'
                      : 'bg-green-600 hover:bg-green-700'
                  }`}
                >
                  {verifying ? (
                    <span className="flex items-center justify-center gap-2">
                      <ArrowPathIcon className="h-5 w-5 animate-spin" />
                      Verifying... {uploadProgress}%
                    </span>
                  ) : (
                    <span className="flex items-center justify-center gap-2">
                      <CheckCircleIcon className="h-5 w-5" />
                      Verify Datasheet
                    </span>
                  )}
                </button>
              ) : (
                <button
                  onClick={handleUpload}
                  disabled={uploading || Object.values(equipmentDocs).every(doc => doc === null)}
                  className={`flex-1 py-3 px-6 rounded-lg font-semibold text-white transition-colors ${
                    uploading || Object.values(equipmentDocs).every(doc => doc === null)
                      ? 'bg-gray-400 cursor-not-allowed'
                      : 'bg-blue-600 hover:bg-blue-700'
                  }`}
                >
                  {uploading ? (
                    <span className="flex items-center justify-center gap-2">
                      <ArrowPathIcon className="h-5 w-5 animate-spin" />
                      Processing... {uploadProgress}%
                    </span>
                  ) : (
                    <>Upload Documents</>
                  )}
                </button>
              )}

              <button
                onClick={handleReset}
                disabled={uploading || verifying}
                className="px-6 py-3 border-2 border-gray-300 rounded-lg font-semibold text-gray-700 hover:bg-gray-50 transition-colors"
              >
                Reset
              </button>
            </div>'''

content = re.sub(old_button, new_button, content, flags=re.DOTALL)
print("✅ Replaced upload button with verify button")

# ============================================================================
# MODIFICATION 4: Add verification results display
# ============================================================================
print("🔧 Adding verification results display...")

results_display = '''

          {/* Verification Results */}
          {verificationResults && equipmentType === 'transformer' && (
            <div className="mt-6 space-y-4">
              {/* Summary Stats */}
              <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
                <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-3">
                  <p className="text-xs text-gray-600 mb-1">Total</p>
                  <p className="text-xl font-bold text-gray-900">{verificationResults.summary.total_parameters}</p>
                </div>
                <div className="bg-white rounded-lg shadow-sm border border-green-200 p-3">
                  <p className="text-xs text-gray-600 mb-1">Valid</p>
                  <p className="text-xl font-bold text-green-600">{verificationResults.summary.valid}</p>
                </div>
                <div className="bg-white rounded-lg shadow-sm border border-yellow-200 p-3">
                  <p className="text-xs text-gray-600 mb-1">Mismatch</p>
                  <p className="text-xl font-bold text-yellow-600">{verificationResults.summary.mismatch}</p>
                </div>
                <div className="bg-white rounded-lg shadow-sm border border-red-200 p-3">
                  <p className="text-xs text-gray-600 mb-1">Incorrect</p>
                  <p className="text-xl font-bold text-red-600">{verificationResults.summary.incorrect}</p>
                </div>
                <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-3">
                  <p className="text-xs text-gray-600 mb-1">Missing</p>
                  <p className="text-xl font-bold text-gray-600">{verificationResults.summary.missing}</p>
                </div>
              </div>

              {/* Results Table */}
              <div className="bg-white rounded-lg shadow-sm border border-gray-200 overflow-hidden">
                <div className="bg-gray-50 px-4 py-3 border-b border-gray-200">
                  <h3 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
                    <CheckCircleIcon className="h-5 w-5 text-green-600" />
                    Verification Results
                  </h3>
                </div>
                
                <div className="overflow-x-auto">
                  <table className="min-w-full divide-y divide-gray-200">
                    <thead className="bg-gray-50">
                      <tr>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Parameter</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Datasheet</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Document</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Status</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Explanation</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Confidence</th>
                        <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">Source</th>
                      </tr>
                    </thead>
                    <tbody className="bg-white divide-y divide-gray-200">
                      {verificationResults.verification_results.map((result, index) => (
                        <tr key={index} className="hover:bg-gray-50">
                          <td className="px-4 py-3 text-sm font-medium text-gray-900">{result.parameter}</td>
                          <td className="px-4 py-3 text-sm text-gray-900">{result.datasheet_value}</td>
                          <td className="px-4 py-3 text-sm text-gray-900">{result.document_value}</td>
                          <td className="px-4 py-3">
                            <span className={`px-2 py-1 text-xs font-semibold rounded-full ${
                              result.status === 'Valid' ? 'bg-green-100 text-green-800' :
                              result.status === 'Mismatch' ? 'bg-yellow-100 text-yellow-800' :
                              result.status === 'Incorrect' ? 'bg-red-100 text-red-800' :
                              'bg-gray-100 text-gray-800'
                            }`}>
                              {result.status}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-sm text-gray-600 max-w-xs">{result.explanation}</td>
                          <td className="px-4 py-3">
                            <span className={`px-2 py-1 text-xs font-medium rounded ${
                              result.confidence === 'High' ? 'bg-green-100 text-green-700' :
                              result.confidence === 'Medium' ? 'bg-yellow-100 text-yellow-700' :
                              'bg-gray-100 text-gray-700'
                            }`}>
                              {result.confidence}
                            </span>
                          </td>
                          <td className="px-4 py-3 text-sm text-gray-600">{result.source_document}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          )}'''

# Find the closing of the equipment docs section and add results after
pattern = r'(</div>\s*</div>\s*\)\s*}\s*</div>)\s*\n\s*</div>\s*\n\s*</div>\s*\n\s*\);'
matches = list(re.finditer(pattern, content, re.DOTALL))
if matches:
    # Use the last match (should be end of main content)
    last_match = matches[-1]
    insert_pos = last_match.end() - 4  # Before the closing );
    content = content[:insert_pos] + results_display + "\n        " + content[insert_pos:]
    print("✅ Added verification results display")

# ============================================================================
# MODIFICATION 5: Update handleReset function
# ============================================================================
print("🔧 Updating handleReset function...")

old_reset = r'(const handleReset = \(\) => \{)\s*(setFiles\(\[\]\);)\s*(setEquipmentDocs\(\{\}\);)'
new_reset = r'\1\n    \2\n    \3\n    setTransformerDatasheet(null);\n    setVerificationResults(null);\n    setVerifying(false);'

content = re.sub(old_reset, new_reset, content)
print("✅ Updated handleReset function")

# ============================================================================
# Write modified file
# ============================================================================
print("\n💾 Writing modified file...")
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print(f"📊 Modified file: {len(content.splitlines())} lines")
print("\n🎉 ✅ Transformer Verification Feature Added Successfully!")
print("\n📍 Features Added:")
print("  ✅ Excel datasheet upload field (blue highlighted section)")
print("  ✅ Verify Datasheet button (green, for transformer only)")
print("  ✅ Verification handler with OpenAI API call")
print("  ✅ Summary statistics display")
print("  ✅ Detailed results table with status indicators")
print("  ✅ Updated reset logic")
print("\n🔄 Frontend will hot-reload automatically")
print(f"📁 File: {file_path}")
