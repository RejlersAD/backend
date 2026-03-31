# Script to add Transformer Verification to ElectricalEquipmentDatasheet.jsx

$file = "C:\Users\Abdullah.Khan\airflow_frontend\src\pages\Engineering\Electrical\ElectricalEquipmentDatasheet.jsx"
$content = Get-Content $file -Raw

Write-Host "🔧 Adding Transformer Verification Feature..." -ForegroundColor Cyan

# Step 2: Add verification handler function (find handleEquipmentDocChange and add after it)
$verificationHandler = @'

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
'@

# Find the position after handleEquipmentDocChange
$handleEquipmentPos = $content.IndexOf("const handleEquipmentDocChange")
if ($handleEquipmentPos -gt 0) {
    $nextFunctionPos = $content.IndexOf("`n  const ", $handleEquipmentPos + 100)
    if ($nextFunctionPos -gt 0) {
        $content = $content.Insert($nextFunctionPos, $verificationHandler)
        Write-Host "✅ Step 2: Added verification handler function" -ForegroundColor Green
    }
}

# Step 3: Add Excel datasheet upload section before PDF documents
$datasheetUpload = @'

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

'@

# Find where to insert - before the EQUIPMENT_DOC_TYPES mapping
$docTypesPos = $content.IndexOf("{EQUIPMENT_DOC_TYPES[equipmentType]?.map((docType) => (")
if ($docTypesPos -gt 0) {
    $content = $content.Insert($docTypesPos, $datasheetUpload)
    Write-Host "✅ Step 3: Added Excel datasheet upload section" -ForegroundColor Green
}

# Step 4: Replace Upload Documents button with Verify button for transformer
$oldButton = @'
            <div className="flex gap-4">
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

              <button
                onClick={handleReset}
                disabled={uploading}
                className="px-6 py-3 border-2 border-gray-300 rounded-lg font-semibold text-gray-700 hover:bg-gray-50 transition-colors"
              >
                Reset
              </button>
            </div>
'@

$newButton = @'
            <div className="flex gap-4">
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
            </div>
'@

$content = $content.Replace($oldButton, $newButton)
Write-Host "✅ Step 4: Updated action buttons" -ForegroundColor Green

# Step 5: Add verification results display (after the action buttons)
$resultsDisplay = @'


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
          )}
'@

# Find position after action buttons section
$actionButtonsEnd = $content.LastIndexOf("</div>", $content.IndexOf("Reset</button>") + 100)
if ($actionButtonsEnd -gt 0) {
    $content = $content.Insert($actionButtonsEnd + 6, $resultsDisplay)
    Write-Host "✅ Step 5: Added verification results display" -ForegroundColor Green
}

# Step 6: Update handleReset function
$oldReset = @'
  const handleReset = () => {
    setFiles([]);
    setEquipmentDocs({});
'@

$newReset = @'
  const handleReset = () => {
    setFiles([]);
    setEquipmentDocs({});
    setTransformerDatasheet(null);
    setVerificationResults(null);
    setVerifying(false);
'@

$content = $content.Replace($oldReset, $newReset)
Write-Host "✅ Step 6: Updated handleReset function" -ForegroundColor Green

# Save the file
Set-Content $file -Value $content

Write-Host "`n🎉 Transformer Verification Feature Added Successfully!" -ForegroundColor Green
Write-Host "📍 File updated: $file" -ForegroundColor Cyan
Write-Host "`n✅ Features Added:" -ForegroundColor Yellow
Write-Host "  - Excel datasheet upload field (blue section)" -ForegroundColor White
Write-Host "  - Verify Datasheet button (green, replaces Upload for transformer)" -ForegroundColor White
Write-Host "  - Verification results display with summary stats" -ForegroundColor White
Write-Host "  - Detailed results table with status indicators" -ForegroundColor White
Write-Host "`n🔄 The frontend will hot-reload automatically" -ForegroundColor Cyan
