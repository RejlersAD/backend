/**
 * 🎯 LINE LIST - BASE EXTRACTION (P&ID Only)
 * 
 * Route: /engineering/process/line-list
 * 
 * Features:
 * - P&ID upload only (PDF)
 * - EXACT same logic as DesignIQ lists (PIDLineExtractorV2 + geometry from-to)
 * - 8 base columns output
 * - HTML table preview after processing
 * - Excel download button
 * - No enrichment (HMB/PMS/NACE/Stress)
 */

import React, { useState, useRef } from 'react';
import { DocumentTextIcon, CloudArrowUpIcon, CheckCircleIcon, ArrowDownTrayIcon } from '@heroicons/react/24/outline';
import { apiClientLongTimeout } from '../../../services/api.service';
import * as XLSX from 'xlsx';

const LineList = () => {
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

  // Handle base extraction (EXACT DesignIQ lists logic)
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
      const response = await apiClientLongTimeout.post(
        '/designiq/lists/base_extraction/',
        formData,
        {
          headers: { 'Content-Type': 'multipart/form-data' },
          timeout: 300000 // 5 minutes
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

  // Export to Excel (EXACT DesignIQ lists format)
  const handleExportExcel = () => {
    if (!extractedData?.data) return;

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

    // Create workbook
    const ws = XLSX.utils.aoa_to_sheet([headers, ...rows]);
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Line List');

    // Auto-size columns
    const colWidths = headers.map((_, colIndex) => {
      const maxWidth = Math.max(
        headers[colIndex].length,
        ...rows.map(row => String(row[colIndex] || '').length)
      );
      return { wch: Math.min(maxWidth + 2, 50) };
    });
    ws['!cols'] = colWidths;

    // Download
    const filename = pidDocument.name.replace('.pdf', '_line_list.xlsx');
    XLSX.writeFile(wb, filename);
  };

  return (
    <div className="min-h-screen bg-gray-50 py-8">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-gray-900 flex items-center gap-3">
            <DocumentTextIcon className="h-8 w-8 text-blue-600" />
            Line List - Base Extraction
          </h1>
          <p className="mt-2 text-gray-600">
            Extract 8 base columns from P&ID only (no enrichment documents)
          </p>
        </div>

        {/* Upload Section */}
        <div className="bg-white rounded-lg shadow-md p-6 mb-6">
          <h2 className="text-xl font-semibold text-gray-900 mb-4">Upload P&ID Document</h2>
          
          {/* P&ID Upload */}
          <div className="mb-6">
            <input
              ref={pidRef}
              type="file"
              accept=".pdf"
              onChange={handlePIDSelect}
              className="hidden"
            />
            <button
              onClick={() => pidRef.current?.click()}
              disabled={isProcessing}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed"
            >
              <CloudArrowUpIcon className="h-5 w-5" />
              Select P&ID PDF
            </button>
            {pidDocument && (
              <div className="mt-2 flex items-center gap-2 text-sm text-green-600">
                <CheckCircleIcon className="h-5 w-5" />
                <span>{pidDocument.name}</span>
              </div>
            )}
          </div>

          {/* Format Selection */}
          <div className="mb-6">
            <label className="block text-sm font-medium text-gray-700 mb-2">
              Line Number Format
            </label>
            <div className="flex gap-4">
              <label className="inline-flex items-center">
                <input
                  type="radio"
                  value="onshore"
                  checked={formatType === 'onshore'}
                  onChange={(e) => setFormatType(e.target.value)}
                  disabled={isProcessing}
                  className="form-radio h-4 w-4 text-blue-600"
                />
                <span className="ml-2">Onshore</span>
              </label>
              <label className="inline-flex items-center">
                <input
                  type="radio"
                  value="offshore"
                  checked={formatType === 'offshore'}
                  onChange={(e) => setFormatType(e.target.value)}
                  disabled={isProcessing}
                  className="form-radio h-4 w-4 text-blue-600"
                />
                <span className="ml-2">Offshore</span>
              </label>
              <label className="inline-flex items-center">
                <input
                  type="radio"
                  value="general"
                  checked={formatType === 'general'}
                  onChange={(e) => setFormatType(e.target.value)}
                  disabled={isProcessing}
                  className="form-radio h-4 w-4 text-blue-600"
                />
                <span className="ml-2">General</span>
              </label>
            </div>
          </div>

          {/* Include Area Toggle */}
          <div className="mb-6">
            <label className="inline-flex items-center">
              <input
                type="checkbox"
                checked={includeArea}
                onChange={(e) => setIncludeArea(e.target.checked)}
                disabled={isProcessing}
                className="form-checkbox h-4 w-4 text-blue-600"
              />
              <span className="ml-2 text-sm text-gray-700">Include Area Code</span>
            </label>
          </div>

          {/* Extract Button */}
          <button
            onClick={handleExtract}
            disabled={!pidDocument || isProcessing}
            className="w-full px-6 py-3 bg-blue-600 text-white font-semibold rounded-lg hover:bg-blue-700 disabled:bg-gray-400 disabled:cursor-not-allowed"
          >
            {isProcessing ? 'Extracting Base Columns...' : 'Extract Base Columns'}
          </button>

          {/* Processing Indicator */}
          {isProcessing && (
            <div className="mt-4">
              <div className="w-full bg-gray-200 rounded-full h-2">
                <div className="bg-blue-600 h-2 rounded-full animate-pulse" style={{ width: '100%' }}></div>
              </div>
              <p className="text-sm text-gray-600 mt-2 text-center">
                Processing P&ID with Multi-Engine OCR + Geometry Detection...
              </p>
            </div>
          )}

          {/* Error Display */}
          {error && (
            <div className="mt-4 p-4 bg-red-50 border border-red-200 rounded-lg">
              <p className="text-red-600 text-sm">{error}</p>
            </div>
          )}
        </div>

        {/* Results Section - HTML Table Preview */}
        {extractedData && extractedData.data && extractedData.data.length > 0 && (
          <div className="bg-white rounded-lg shadow-md p-6">
            {/* Results Header with Download Button */}
            <div className="flex items-center justify-between mb-4">
              <div>
                <h2 className="text-xl font-semibold text-gray-900">
                  Extraction Results
                </h2>
                <p className="text-sm text-gray-600 mt-1">
                  {extractedData.total_lines} lines extracted • 8 base columns
                </p>
              </div>
              <button
                onClick={handleExportExcel}
                className="flex items-center gap-2 px-4 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700"
              >
                <ArrowDownTrayIcon className="h-5 w-5" />
                Download Excel
              </button>
            </div>

            {/* HTML Table Preview (EXACT DesignIQ lists style) */}
            <div className="overflow-x-auto border border-gray-300 rounded-lg">
              <table className="min-w-full divide-y divide-gray-300">
                <thead className="bg-blue-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-900 uppercase tracking-wider border-r border-gray-300">#</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-900 uppercase tracking-wider border-r border-gray-300">Original Detection</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-900 uppercase tracking-wider border-r border-gray-300">Fluid Code</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-900 uppercase tracking-wider border-r border-gray-300">Size</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-900 uppercase tracking-wider border-r border-gray-300">Sequence No</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-900 uppercase tracking-wider border-r border-gray-300">PIPR Class</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-900 uppercase tracking-wider border-r border-gray-300">Insulation</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-900 uppercase tracking-wider border-r border-gray-300">From</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold text-gray-900 uppercase tracking-wider">To</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {extractedData.data.map((row, index) => (
                    <tr key={index} className="hover:bg-gray-50">
                      <td className="px-4 py-3 text-sm text-gray-900 border-r border-gray-200">{index + 1}</td>
                      <td className="px-4 py-3 text-sm text-gray-900 border-r border-gray-200 font-medium">{row.original_detection || '-'}</td>
                      <td className="px-4 py-3 text-sm text-gray-900 border-r border-gray-200">{row.fluid_code || '-'}</td>
                      <td className="px-4 py-3 text-sm text-gray-900 border-r border-gray-200">{row.size || '-'}</td>
                      <td className="px-4 py-3 text-sm text-gray-900 border-r border-gray-200">{row.sequence_no || '-'}</td>
                      <td className="px-4 py-3 text-sm text-gray-900 border-r border-gray-200">{row.pipr_class || '-'}</td>
                      <td className="px-4 py-3 text-sm text-gray-900 border-r border-gray-200">{row.insulation || '-'}</td>
                      <td className="px-4 py-3 text-sm text-gray-900 border-r border-gray-200">{row.from || '-'}</td>
                      <td className="px-4 py-3 text-sm text-gray-900">{row.to || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Footer Info */}
            <div className="mt-4 p-4 bg-blue-50 border border-blue-200 rounded-lg">
              <div className="flex items-center gap-2 text-sm text-blue-800">
                <CheckCircleIcon className="h-5 w-5 text-blue-600" />
                <span>
                  <strong>Base Extraction Complete:</strong> {extractedData.total_lines} lines extracted using EXACT DesignIQ lists logic 
                  (PIDLineExtractorV2 + Geometry-based FROM-TO detection)
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

export default LineList;
