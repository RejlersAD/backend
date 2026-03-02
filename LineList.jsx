/**
 * 🎯 LINE LIST - BASE EXTRACTION LAYER ONLY
 * 
 * Purpose: Extract base 8 columns from P&ID (no enrichment)
 * Route: /engineering/process/line-list
 * 
 * Features:
 * - P&ID upload only (PDF)
 * - Synchronous processing
 * - 8 locked columns output
 * - No HMB/PMS/NACE/Stress documents
 * - Stable, lightweight, production-ready
 */

import React, { useState, useRef } from 'react';
import { DocumentTextIcon, CloudArrowUpIcon, CheckCircleIcon } from '@heroicons/react/24/outline';
import { apiClientLongTimeout } from '../../../services/api.service';

const LineList = () => {
  // State management
  const [pidDocument, setPidDocument] = useState(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const [extractedData, setExtractedData] = useState(null);
  const [error, setError] = useState(null);
  const [formatType, setFormatType] = useState('onshore');
  const [includeArea, setIncludeArea] = useState(false);
  
  const pidRef = useRef(null);

  // Handle P&ID file selection
  const handlePIDSelect = (e) => {
    const file = e.target.files[0];
    if (file && file.type === 'application/pdf') {
      setPidDocument(file);
      setError(null);
      setExtractedData(null);
    } else {
      setError('Please select a valid PDF file');
    }
  };

  // Handle base extraction
  const handleExtract = async () => {
    if (!pidDocument) {
      setError('Please upload a P&ID document first');
      return;
    }

    setIsProcessing(true);
    setError(null);
    setExtractedData(null);

    const formData = new FormData();
    formData.append('pid_file', pidDocument);
    formData.append('format_type', formatType);
    formData.append('include_area', includeArea);

    try {
      // Use long timeout client (20 minutes) for OCR initialization + extraction
      const response = await apiClientLongTimeout.post(
        '/designiq/lists/base_extraction/',
        formData,
        {
          headers: { 'Content-Type': 'multipart/form-data' }
          // No timeout override - uses default 20 minutes from apiClientLongTimeout
        }
      );

      if (response.data.success) {
        setExtractedData(response.data);
      } else {
        setError(response.data.message || 'Extraction failed');
      }
    } catch (err) {
      console.error('Extraction error:', err);
      setError(err.response?.data?.error || err.message || 'Extraction failed');
    } finally {
      setIsProcessing(false);
    }
  };

  // Export to Excel
  const handleExport = () => {
    if (!extractedData?.data) return;

    // Create CSV content
    const headers = ['Original Detection', 'Fluid Code', 'Size', 'Sequence No', 'PIPR Class', 'Insulation', 'From', 'To'];
    const rows = extractedData.data.map(item => [
      item.original_detection || '',
      item.fluid_code || '',
      item.size || '',
      item.sequence_no || '',
      item.pipr_class || '',
      item.insulation || '',
      item.from || '',
      item.to || ''
    ]);

    const csvContent = [
      headers.join(','),
      ...rows.map(row => row.map(cell => `"${cell}"`).join(','))
    ].join('\n');

    // Download CSV
    const blob = new Blob([csvContent], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `line-list-${new Date().toISOString().split('T')[0]}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-gray-900 flex items-center gap-3">
            <DocumentTextIcon className="h-8 w-8 text-blue-600" />
            Line List - Base Extraction
          </h1>
          <p className="mt-2 text-gray-600">
            Extract 8 base columns from P&ID (P&ID-only, no enrichment)
          </p>
        </div>

        {/* Upload Section */}
        <div className="bg-white rounded-lg shadow-sm p-6 mb-6">
          <h2 className="text-xl font-semibold text-gray-800 mb-4">1. Upload P&ID Document</h2>
          
          {/* P&ID Upload */}
          <div className="border-2 border-dashed border-gray-300 rounded-lg p-6 hover:border-blue-400 transition-colors">
            <input
              ref={pidRef}
              type="file"
              accept=".pdf"
              onChange={handlePIDSelect}
              className="hidden"
            />
            <button
              onClick={() => pidRef.current?.click()}
              className="w-full flex flex-col items-center justify-center gap-3"
              disabled={isProcessing}
            >
              {pidDocument ? (
                <>
                  <CheckCircleIcon className="h-12 w-12 text-green-500" />
                  <span className="text-sm font-medium text-gray-700">{pidDocument.name}</span>
                  <span className="text-xs text-gray-500">
                    {(pidDocument.size / 1024 / 1024).toFixed(2)} MB
                  </span>
                </>
              ) : (
                <>
                  <CloudArrowUpIcon className="h-12 w-12 text-gray-400" />
                  <span className="text-sm font-medium text-gray-700">
                    Click to select P&ID (PDF only)
                  </span>
                </>
              )}
            </button>
          </div>

          {/* Format Options */}
          <div className="mt-4 space-y-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-2">
                Line Number Format
              </label>
              <select
                value={formatType}
                onChange={(e) => setFormatType(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-md focus:ring-2 focus:ring-blue-500"
                disabled={isProcessing}
              >
                <option value="onshore">Onshore (SIZE-FLUID-SEQ-CLASS)</option>
                <option value="offshore">Offshore (AREA-FLUID-SIZE-CLASS-SEQ)</option>
                <option value="general">General (Auto-detect)</option>
              </select>
            </div>

            <div className="flex items-center">
              <input
                type="checkbox"
                id="includeArea"
                checked={includeArea}
                onChange={(e) => setIncludeArea(e.target.checked)}
                className="h-4 w-4 text-blue-600 focus:ring-blue-500 border-gray-300 rounded"
                disabled={isProcessing}
              />
              <label htmlFor="includeArea" className="ml-2 text-sm text-gray-700">
                Include Area Code in line number (SIZE"-AREA-FLUID-SEQ-CLASS)
              </label>
            </div>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex gap-4 mb-6">
          <button
            onClick={handleExtract}
            disabled={!pidDocument || isProcessing}
            className={`flex-1 py-3 px-6 rounded-lg font-semibold text-white transition-all ${
              !pidDocument || isProcessing
                ? 'bg-gray-300 cursor-not-allowed'
                : 'bg-blue-600 hover:bg-blue-700 shadow-lg hover:shadow-xl'
            }`}
          >
            {isProcessing ? (
              <span className="flex items-center justify-center gap-2">
                <svg className="animate-spin h-5 w-5" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" fill="none" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z" />
                </svg>
                Processing...
              </span>
            ) : (
              'Extract Base Columns'
            )}
          </button>

          {extractedData && (
            <button
              onClick={handleExport}
              className="px-6 py-3 bg-green-600 text-white rounded-lg font-semibold hover:bg-green-700 transition-all shadow-lg hover:shadow-xl"
            >
              Export to CSV
            </button>
          )}
        </div>

        {/* Error Display */}
        {error && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-4 mb-6">
            <p className="text-red-800 font-medium">Error: {error}</p>
          </div>
        )}

        {/* Results Table */}
        {extractedData && (
          <div className="bg-white rounded-lg shadow-sm overflow-hidden">
            <div className="px-6 py-4 bg-gray-50 border-b border-gray-200">
              <h2 className="text-xl font-semibold text-gray-800">
                Extracted {extractedData.total_lines} Lines ({extractedData.columns} Columns)
              </h2>
            </div>

            <div className="overflow-x-auto">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-100">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-700 uppercase tracking-wider">
                      #
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-700 uppercase tracking-wider">
                      Original Detection
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-700 uppercase tracking-wider">
                      Fluid Code
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-700 uppercase tracking-wider">
                      Size
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-700 uppercase tracking-wider">
                      Sequence No
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-700 uppercase tracking-wider">
                      PIPR Class
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-700 uppercase tracking-wider">
                      Insulation
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-700 uppercase tracking-wider">
                      From
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-700 uppercase tracking-wider">
                      To
                    </th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {extractedData.data.map((line, idx) => (
                    <tr key={idx} className="hover:bg-gray-50 transition-colors">
                      <td className="px-4 py-3 text-sm text-gray-500">{idx + 1}</td>
                      <td className="px-4 py-3 text-sm font-medium text-gray-900">
                        {line.original_detection || '-'}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-700">
                        {line.fluid_code || '-'}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-700">
                        {line.size || '-'}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-700">
                        {line.sequence_no || '-'}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-700">
                        {line.pipr_class || '-'}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-700">
                        {line.insulation || '-'}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-700">
                        {line.from || '-'}
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-700">
                        {line.to || '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* Info Panel */}
        {!extractedData && !isProcessing && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-6 mt-6">
            <h3 className="text-lg font-semibold text-blue-900 mb-3">ℹ️ About Base Extraction</h3>
            <ul className="space-y-2 text-blue-800">
              <li className="flex items-start gap-2">
                <span className="font-bold">•</span>
                <span><strong>P&ID Only:</strong> Upload P&ID document (PDF format)</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="font-bold">•</span>
                <span><strong>8 Columns:</strong> Original Detection, Fluid Code, Size, Sequence No, PIPR Class, Insulation, From, To</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="font-bold">•</span>
                <span><strong>Synchronous Processing:</strong> Results returned immediately (no background tasks)</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="font-bold">•</span>
                <span><strong>No Enrichment:</strong> This page does not include HMB, PMS, NACE, or Stress Criticality documents</span>
              </li>
              <li className="flex items-start gap-2">
                <span className="font-bold">•</span>
                <span><strong>AI-Powered FROM-TO:</strong> Uses Computer Vision + OpenAI to detect flow direction</span>
              </li>
            </ul>
          </div>
        )}
      </div>
    </div>
  );
};

export default LineList;
