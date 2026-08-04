import re

file_path = r"C:\Users\Abdullah.Khan\airflow_frontend\src\pages\Engineering\Electrical\ElectricalEquipmentDatasheet.jsx"

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

print("🔧 Adding verification results display...")

# Find position - before the final closing div and );
# Look for the reset button section and add after it
pattern = r'(<button\s+onClick={handleReset}.*?Reset\s*</button>\s*</div>)'

results_display = r'''

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

# Add results display after the reset button
content = re.sub(pattern, r'\1' + results_display, content, flags=re.DOTALL)

with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("✅ Verification results section added successfully!")
print(f"📊 Final file: {len(content.splitlines())} lines")
