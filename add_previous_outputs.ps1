# Script to add Previous Outputs feature to LineList.jsx

$filePath = "C:\Users\Abdullah.Khan\airflow_frontend\src\pages\Engineering\Process\LineList.jsx"
$content = Get-Content $filePath -Raw

# Add fetch function after pidRef declaration
$fetchFunction = @"
  const pidRef = useRef(null);

  // Fetch previous outputs
  const fetchPreviousOutputs = useCallback(async () => {
    setLoadingOutputs(true);
    try {
      const response = await apiClientLongTimeout.get(
        `$${API_BASE_URL}/designiq/lists/previous_outputs/?list_type=line_list`
      );
      if (response.data.success) {
        setPreviousOutputs(response.data.outputs || []);
      }
    } catch (err) {
      console.error('Error fetching previous outputs:', err);
    } finally {
      setLoadingOutputs(false);
    }
  }, []);

  // Load previous outputs on mount
  useEffect(() => {
    fetchPreviousOutputs();
  }, [fetchPreviousOutputs]);
"@

$content = $content -replace [regex]::Escape("  const pidRef = useRef(null);"), $fetchFunction

# Add Previous Outputs section before closing </div></div>
$prevOutputsSection = @"
        {/* Previous Outputs Section */}
        <div className="bg-white rounded-lg shadow-sm p-6 mt-6">
          <div className="flex items-center justify-between mb-4">
            <div>
              <h3 className="text-lg font-bold text-blue-900">📂 Previous Outputs</h3>
              <p className="text-sm text-blue-700">Download previously processed Line List Excel files</p>
            </div>
            <button
              onClick={fetchPreviousOutputs}
              disabled={loadingOutputs}
              className="px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:opacity-50 text-sm font-medium"
            >
              {loadingOutputs ? '🔄 Refreshing...' : '🔄 Refresh'}
            </button>
          </div>

          <div className="space-y-3">
            {loadingOutputs ? (
              <div className="flex items-center justify-center py-8">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-blue-600"></div>
                <span className="ml-3 text-gray-600">Loading previous outputs...</span>
              </div>
            ) : previousOutputs.length === 0 ? (
              <div className="text-center py-8 bg-blue-50 rounded-lg border border-blue-100">
                <DocumentTextIcon className="h-12 w-12 text-blue-300 mx-auto mb-3" />
                <p className="text-gray-600 font-medium">No previous outputs found</p>
                <p className="text-sm text-gray-500 mt-1">Process a P&ID to see results here</p>
              </div>
            ) : (
              <div className="space-y-2">
                <div className="grid grid-cols-12 gap-4 px-4 py-2 bg-gray-50 rounded font-semibold text-sm text-gray-700">
                  <div className="col-span-3">P&ID Number</div>
                  <div className="col-span-2">Revision</div>
                  <div className="col-span-2">Lines</div>
                  <div className="col-span-2">Date</div>
                  <div className="col-span-3 text-right">Actions</div>
                </div>
                {previousOutputs.map((output) => (
                  <div key={output.id} className="grid grid-cols-12 gap-4 px-4 py-3 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors">
                    <div className="col-span-3 font-medium text-gray-900">{output.pid_number || 'N/A'}</div>
                    <div className="col-span-2 text-gray-600">{output.pid_revision || '-'}</div>
                    <div className="col-span-2 text-gray-600">{output.total_lines || 0} lines</div>
                    <div className="col-span-2 text-gray-500 text-sm">
                      {output.processing_date ? new Date(output.processing_date).toLocaleDateString() : 'N/A'}
                    </div>
                    <div className="col-span-3 text-right">
                      <a
                        href={`$${API_BASE_URL}/media/$${output.excel_file}`}
                        download
                        className="inline-flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 text-sm font-medium"
                      >
                        <CloudArrowUpIcon className="h-4 w-4" />
                        Download Excel
                      </a>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
"@

$content = $content -replace [regex]::Escape("      </div>`n    </div>"), $prevOutputsSection

$content | Set-Content $filePath
Write-Host "✓ Successfully added Previous Outputs feature to LineList.jsx"
