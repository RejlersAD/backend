import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import {
  ViewColumnsIcon,
  CubeIcon,
  LinkIcon,
  BellAlertIcon,
  PlusIcon,
  MagnifyingGlassIcon,
  ArrowDownTrayIcon,
  ArrowUpTrayIcon,
  CheckCircleIcon,
  XCircleIcon,
  ClockIcon,
  FunnelIcon
} from '@heroicons/react/24/outline';
import { usePageControls } from '../../hooks/usePageControls';
import { PageControlButtons } from '../../components/Common/PageControlButtons';
import { STORAGE_KEYS } from '../../config/app.config';
import { API_BASE_URL } from '../../config/api.config';
import { apiClientLongTimeout } from '../../services/api.service';
import * as XLSX from 'xlsx';

// List types configuration (matches backend)
const LIST_TYPES = {
  line_list: {
    name: 'Line List',
    icon: ViewColumnsIcon,
    description: 'Piping line specifications and attributes',
    color: 'blue',
    fields: ['line_number', 'service', 'size', 'rating', 'material']
  },
  equipment_list: {
    name: 'Equipment List',
    icon: CubeIcon,
    description: 'Equipment specifications and details',
    color: 'green',
    fields: ['tag_number', 'description', 'type', 'capacity', 'duty']
  },
  tie_in_list: {
    name: 'Tie-In List',
    icon: LinkIcon,
    description: 'Connection points and tie-in specifications',
    color: 'purple',
    fields: ['tie_in_number', 'location', 'size', 'type', 'connection_details']
  },
  alarm_trip_list: {
    name: 'Alarm/Trip List',
    icon: BellAlertIcon,
    description: 'Safety alarms and trip setpoints',
    color: 'red',
    fields: ['tag', 'description', 'alarm_type', 'setpoint', 'action']
  }
};

const STATUS_COLORS = {
  active: 'green',
  pending: 'yellow',
  approved: 'blue',
  rejected: 'red',
  inactive: 'gray'
};

const DesignIQLists = () => {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const fileInputRef = useRef(null);
  const fileInputWithAreaRef = useRef(null);
  const fileInputOffshoreRef = useRef(null);
  const [selectedListType, setSelectedListType] = useState(searchParams.get('type') || 'line_list');
  const [items, setItems] = useState([]);
  const [stats, setStats] = useState(null);
  const [loading, setLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [searchTerm, setSearchTerm] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [showFilters, setShowFilters] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);
  const [uploadingPID, setUploadingPID] = useState(false);
  const [uploadResult, setUploadResult] = useState(null);
  const [processing, setProcessing] = useState(false);
  const [showPreviewModal, setShowPreviewModal] = useState(false);
  const [extractedData, setExtractedData] = useState(null);
  const [showProcessingModal, setShowProcessingModal] = useState(false);
  const [processingProgress, setProcessingProgress] = useState({ step: '', percent: 0 });
  
  // Previous Outputs (Historical Download Feature)
  const [previousOutputs, setPreviousOutputs] = useState([]);
  const [loadingOutputs, setLoadingOutputs] = useState(false);
  
  // Enrichment Documents (Optional - Do NOT block base extraction)
  const [pidDocument, setPidDocument] = useState(null);
  const [hmbDocument, setHmbDocument] = useState(null);
  const [pmsDocument, setPmsDocument] = useState(null);
  const [naceDocument, setNaceDocument] = useState(null);
  const [stressCriticalityDocument, setStressCriticalityDocument] = useState(null);
  const [selectedFormat, setSelectedFormat] = useState(null);
  const hmbRef = useRef(null);
  const pmsRef = useRef(null);
  const naceRef = useRef(null);
  const stressRef = useRef(null);
  
  // Line Number Format Configuration
  const STRICT_LINE_PATTERNS = {
    line_size: '\\d{1,2}',
    area: '\\d{2,3}',
    fluid_code: '[A-Z]{1,3}',
    sequence_no: '\\d{3,5}',
    pipe_class: '[A-Z0-9]{3,6}',
    insulation: '[A-Z]{1,2}'
  };
  const [showFormatConfigModal, setShowFormatConfigModal] = useState(false);
  const [lineNumberFormat, setLineNumberFormat] = useState({
    template: '',  // e.g., "SIZE-AREA-FLUID-SEQUENCE-PIPECLASS-INSULATION"
    components: [
      { id: 'line_size', name: 'Line Size', enabled: true, order: 1, pattern: '\\d{1,2}', example: '36' },
      { id: 'area', name: 'Area', enabled: false, order: 2, pattern: '\\d{2,3}', example: '41' },
      { id: 'fluid_code', name: 'Fluid Code', enabled: true, order: 3, pattern: '[A-Z]{1,3}', example: 'SWR' },
      { id: 'sequence_no', name: 'Sequence No', enabled: true, order: 4, pattern: '\\d{3,5}', example: '60302' },
      { id: 'pipe_class', name: 'Pipe Class', enabled: true, order: 5, pattern: '[A-Z0-9]{3,6}', example: 'A2AU16' },
      { id: 'insulation', name: 'Insulation', enabled: false, order: 6, pattern: '[A-Z]{1,2}', example: 'V' }
    ],
    separator: '-',  // Separator between components
    allowVariableSeparators: true  // Allow -, –, —, etc.
  });

  const currentListConfig = LIST_TYPES[selectedListType];

  // Page controls (Fullscreen, Sidebar, Auto-refresh)
  const pageControls = usePageControls({
    refreshCallback: () => fetchData(true),
    autoRefreshInterval: 30000, // 30 seconds
    storageKey: `designiq_lists_${selectedListType}`,
  });

  // Load saved line format configuration from localStorage
  useEffect(() => {
    const savedConfig = localStorage.getItem('designiq_line_format_config');
    if (savedConfig) {
      try {
        const parsed = JSON.parse(savedConfig);
        const normalized = {
          ...parsed,
          components: (parsed.components || []).map((component) => ({
            ...component,
            pattern: STRICT_LINE_PATTERNS[component.id] || component.pattern
          }))
        };
        setLineNumberFormat(normalized);
      } catch (error) {
        console.error('Error loading saved format config:', error);
      }
    }
  }, []);

  // Fetch previous outputs for download
  const fetchPreviousOutputs = useCallback(async () => {
    if (selectedListType !== 'line_list') return; // Only for line_list
    
    setLoadingOutputs(true);
    try {
      const token = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
      const response = await fetch(
        `${API_BASE_URL}/designiq/lists/previous_outputs/?list_type=${selectedListType}`,
        {
          headers: {
            'Authorization': `Bearer ${token}`
          }
        }
      );
      
      if (response.ok) {
        const data = await response.json();
        setPreviousOutputs(data.outputs || []);
      }
    } catch (error) {
      console.error('Error fetching previous outputs:', error);
    } finally {
      setLoadingOutputs(false);
    }
  }, [selectedListType]);

  const fetchData = useCallback(async (isAutoRefresh = false) => {
    if (isAutoRefresh) setIsRefreshing(true);
    else setLoading(true);
    try {
      const token = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
      
      // Fetch items
      let itemsUrl = `${API_BASE_URL}/designiq/lists/?list_type=${selectedListType}`;
      if (statusFilter !== 'all') {
        itemsUrl += `&status=${statusFilter}`;
      }
      if (searchTerm) {
        itemsUrl += `&search=${searchTerm}`;
      }

      const itemsResponse = await fetch(itemsUrl, {
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        }
      });
      
      if (itemsResponse.ok) {
        const itemsData = await itemsResponse.json();
        // API returns either array directly or object with results property (DRF pagination)
        setItems(Array.isArray(itemsData) ? itemsData : (itemsData.results || []));
      } else {
        setItems([]);
      }

      // Fetch stats
      const statsResponse = await fetch(
        `${API_BASE_URL}/designiq/lists/stats/?list_type=${selectedListType}`,
        {
          headers: {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
          }
        }
      );
      
      if (statsResponse.ok) {
        const statsData = await statsResponse.json();
        setStats(statsData);
      }
    } catch (error) {
      console.error('Error fetching data:', error);
      setItems([]);
      setStats(null);
    } finally {
      setLoading(false);
      setIsRefreshing(false);
    }
  }, [selectedListType, statusFilter, searchTerm]);

  useEffect(() => {
    fetchData();
    fetchPreviousOutputs();
  }, [fetchData, fetchPreviousOutputs]);

  const handleSearch = () => {
    fetchData();
  };
  
  // Download historical Excel output
  const handleDownloadOutput = async (outputId, filename) => {
    try {
      const token = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
      const response = await fetch(
        `${API_BASE_URL}/designiq/lists/download_output/${outputId}/`,
        {
          headers: {
            'Authorization': `Bearer ${token}`
          }
        }
      );
      
      if (response.ok) {
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        a.click();
        window.URL.revokeObjectURL(url);
      }
    } catch (error) {
      console.error('Error downloading output:', error);
      alert('Failed to download file');
    }
  };

  const pollTaskStatus = async (taskId) => {
    const maxAttempts = 120; // 10 minutes (5 sec intervals)
    let attempts = 0;
    
    const poll = async () => {
      try {
        attempts++;
        const token = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
        const response = await fetch(`${API_BASE_URL}/designiq/lists/upload_pid_status/${taskId}/`, {
          headers: { 'Authorization': `Bearer ${token}` }
        });
        
        if (!response.ok) throw new Error('Failed to check status');
        
        const status = await response.json();
        console.log(`📊 Poll ${attempts}/${maxAttempts}: ${status.state} - ${status.status}`);
        
        // Update progress if available
        if (status.percent) {
          setProcessingProgress({ 
            step: status.status || 'Processing...', 
            percent: Math.min(status.percent, 99) 
          });
        }
        
        if (status.state === 'SUCCESS' && status.result) {
          // Task completed successfully
          console.log('✅ Task completed:', status.result);
          setProcessingProgress({ step: '✅ Complete!', percent: 100 });
          
          if (status.result.enriched_data && status.result.enriched_data.length > 0) {
            setExtractedData({ lines: status.result.enriched_data, isEnriched: true });
            setShowPreviewModal(true);
          } else if (status.result.extracted_lines && status.result.extracted_lines.length > 0) {
            setExtractedData({ lines: status.result.extracted_lines, isEnriched: false });
            setShowPreviewModal(true);
          }
          
          setUploadResult({
            success: true,
            message: status.result.message || 'Processing complete',
            total_lines: status.result.total_items || 0,
            enriched: status.result.enriched_data ? true : false
          });
          
          await fetchData();
          return;
        } else if (status.state === 'FAILURE') {
          throw new Error(status.error || 'Task failed');
        } else if (status.state === 'PENDING' || status.state === 'PROCESSING') {
          // Continue polling
          if (attempts < maxAttempts) {
            setTimeout(() => poll(), 5000); // Poll every 5 seconds
          } else {
            throw new Error('Timeout: Processing took too long');
          }
        }
      } catch (error) {
        console.error('Polling error:', error);
        setUploadResult({
          success: false,
          message: error.message || 'Failed to check processing status'
        });
        setShowProcessingModal(false);
        setProcessing(false);
        setUploadingPID(false);
      }
    };
    
    await poll();
  };

  const processAllDocuments = async () => {
    if (!selectedFormat) {
      setUploadResult({
        success: false,
        message: 'Please select project format (Onshore/Offshore/General) before uploading.'
      });
      return;
    }
    
    if (!pidDocument || !hmbDocument || !pmsDocument || !naceDocument || !stressCriticalityDocument) {
      setUploadResult({
        success: false,
        message: 'Please upload all 5 documents before processing.'
      });
      return;
    }

    setUploadingPID(true);
    setProcessing(true);
    setUploadResult(null);
    setShowProcessingModal(true);
    setProcessingProgress({ step: 'Initializing 5-document enrichment...', percent: 5 });
    
    // Progress simulation for user feedback (backend does actual processing)
    setTimeout(() => setProcessingProgress({ step: '📤 Uploading P&ID + HMB + PMS + NACE...', percent: 10 }), 2000);
    setTimeout(() => setProcessingProgress({ step: '📄 Running OCR on P&ID drawing...', percent: 30 }), 8000);
    setTimeout(() => setProcessingProgress({ step: '🔍 Extracting 8 base columns...', percent: 50 }), 15000);
    setTimeout(() => setProcessingProgress({ step: '📊 Analyzing HMB (process data)...', percent: 65 }), 25000);
    setTimeout(() => setProcessingProgress({ step: '🔧 Analyzing PMS (materials)...', percent: 75 }), 35000);
    setTimeout(() => setProcessingProgress({ step: '⚗️ Analyzing NACE (corrosion)...', percent: 85 }), 45000);
    setTimeout(() => setProcessingProgress({ step: '🤖 AI enrichment (+26 columns)...', percent: 93 }), 60000);
    setTimeout(() => setProcessingProgress({ step: '✨ Finalizing 34-column table...', percent: 97 }), 75000);
    
    try {
      const formData = new FormData();
      formData.append('pid_file', pidDocument);
      formData.append('list_type', 'line_list');
      
      // CRITICAL MAPPING: Format type determines regex pattern and area handling
      // onshore → format_type='onshore', include_area=false
      //   Pattern: SIZE-FLUID-SEQUENCE-PIPECLASS
      //   Example: 2-D-5777-033842-X
      //
      // general → format_type='general', include_area=true
      //   Pattern: SIZE"-AREA-FLUID-SEQUENCE-PIPECLASS
      //   Example: 1"-41-SWS-64544-A2AU16-V
      //
      // offshore → format_type='offshore', include_area=true
      //   Pattern: AREA-FLUID-SIZE-PIPECLASS-SEQUENCE
      //   Example: 604-HO-8-BC2GA0-1070-H
      const includeArea = (selectedFormat === 'offshore' || selectedFormat === 'general');
      formData.append('include_area', includeArea ? 'true' : 'false');
      formData.append('format_type', selectedFormat);
      
      // Add line number format configuration
      const enabledComponents = lineNumberFormat.components
        .filter(c => c.enabled)
        .sort((a, b) => a.order - b.order);
      
      formData.append('line_format_config', JSON.stringify({
        components: enabledComponents.map(c => ({
          id: c.id,
          name: c.name,
          order: c.order,
          pattern: STRICT_LINE_PATTERNS[c.id] || c.pattern
        })),
        separator: lineNumberFormat.separator,
        allowVariableSeparators: lineNumberFormat.allowVariableSeparators
      }));

      // ENRICHMENT LAYER: Add all 5 documents for 35-column extraction
      formData.append('hmb_file', hmbDocument);
      formData.append('pms_file', pmsDocument);
      formData.append('nace_file', naceDocument);
      formData.append('stress_criticality_file', stressCriticalityDocument);
      console.log('[5-Doc Enrichment] All documents attached for 35-column extraction');

      const token = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
      
      if (!token) {
        setUploadResult({
          success: false,
          message: 'Authentication token not found. Please log in again.'
        });
        setUploadingPID(false);
        setProcessing(false);
        return;
      }

      // Create AbortController for timeout control (20 minutes for large files + AI processing)
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 1200000); // 20 minutes

      const response = await fetch(`${API_BASE_URL}/designiq/lists/upload_pid/`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`
        },
        body: formData,
        signal: controller.signal
      });

      clearTimeout(timeoutId);

      if (response.ok) {
        const data = await response.json();
        console.log('Upload response:', data);
        console.log('Response keys:', Object.keys(data));
        console.log('Has enriched_data?', !!data.enriched_data, 'Length:', data.enriched_data?.length);
        console.log('Has extracted_lines?', !!data.extracted_lines, 'Length:', data.extracted_lines?.length);
        
        // If task_id returned, it's async processing - poll for results
        if (data.task_id && !data.enriched_data && !data.extracted_lines) {
          console.log(`🔄 Async mode: Task ${data.task_id} queued, starting polling...`);
          setProcessingProgress({ step: '⏳ Processing in background...', percent: 50 });
          await pollTaskStatus(data.task_id);
          return;
        }
        
        // Direct result (EAGER mode or task completed)
        setShowProcessingModal(false);
        setProcessing(false);
        setUploadingPID(false);
        
        if (data.enriched_data && data.enriched_data.length > 0) {
          console.log(`✓ ENRICHED: ${data.enriched_data.length} lines with 34 columns`);
          setExtractedData({ lines: data.enriched_data, isEnriched: true });
          setShowPreviewModal(true);
        } else if (data.extracted_lines && data.extracted_lines.length > 0) {
          console.log(`✓ BASE: ${data.extracted_lines.length} lines with 8 columns`);
          setExtractedData({ lines: data.extracted_lines, isEnriched: false });
          setShowPreviewModal(true);
        } else {
          console.warn('⚠️ No data in response:', data);
        }
        
        setUploadResult({
          success: true,
          message: data.message || 'Processing complete',
          task_id: data.task_id,
          total_lines: data.total_lines || 0,
          enriched: data.enriched_data ? true : false
        });
        
        await fetchData();
      } else {
        const errorData = await response.json().catch(() => ({ error: 'Upload failed' }));
        setUploadResult({
          success: false,
          message: errorData.error || 'Upload failed'
        });
      }
    } catch (error) {
      console.error('Error during processing:', error);
      
      let errorMessage = 'An error occurred during processing';
      if (error.name === 'AbortError') {
        errorMessage = 'Processing timeout (20 min exceeded). Files may be too large or backend overloaded.';
      } else if (error.message.includes('fetch')) {
        errorMessage = 'Network error: Unable to connect to server. Please check if backend is running.';
      } else {
        errorMessage = error.message || errorMessage;
      }
      
      setUploadResult({
        success: false,
        message: errorMessage
      });
    } finally {
      setUploadingPID(false);
      setProcessing(false);
      setShowProcessingModal(false);
      setProcessingProgress({ step: '', percent: 0 });
    }
  };

  const handlePIDUpload = async (event, includeArea = false, formatType = 'onshore') => {
    const file = event.target.files?.[0];
    if (!file) return;

    if (file.type !== 'application/pdf') {
      setUploadResult({
        success: false,
        message: 'Please upload a PDF file'
      });
      return;
    }

    setUploadingPID(true);
    setProcessing(true);
    setUploadResult(null);
    
    try {
      const formData = new FormData();
      formData.append('pid_file', file);
      formData.append('list_type', 'line_list');
      formData.append('include_area', includeArea ? 'true' : 'false');
      formData.append('format_type', formatType);
      
      // Add line number format configuration
      const enabledComponents = lineNumberFormat.components
        .filter(c => c.enabled)
        .sort((a, b) => a.order - b.order);
      
      formData.append('line_format_config', JSON.stringify({
        components: enabledComponents.map(c => ({
          id: c.id,
          name: c.name,
          order: c.order,
          pattern: STRICT_LINE_PATTERNS[c.id] || c.pattern
        })),
        separator: lineNumberFormat.separator,
        allowVariableSeparators: lineNumberFormat.allowVariableSeparators
      }));

      // ENRICHMENT LAYER (Optional - Does NOT block base extraction)
      if (hmbDocument) {
        formData.append('hmb_file', hmbDocument);
        console.log('[Enrichment] HMB document attached:', hmbDocument.name);
      }
      if (pmsDocument) {
        formData.append('pms_file', pmsDocument);
        console.log('[Enrichment] PMS document attached:', pmsDocument.name);
      }
      if (naceDocument) {
        formData.append('nace_file', naceDocument);
        console.log('[Enrichment] NACE document attached:', naceDocument.name);
      }
      if (stressCriticalityDocument) {
        formData.append('stress_criticality_file', stressCriticalityDocument);
        console.log('[Enrichment] Stress Criticality document attached:', stressCriticalityDocument.name);
      }

      const token = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
      
      if (!token) {
        setUploadResult({
          success: false,
          message: 'Authentication token not found. Please log in again.'
        });
        return;
      }

      console.log('[P&ID Upload] ÃƒÂ°Ã…Â¸Ã…Â¡Ã¢â€šÂ¬ Starting upload with extended timeout (10 minutes)...');
      console.log('[P&ID Upload] File:', file.name, 'Size:', (file.size / 1024 / 1024).toFixed(2), 'MB');

      // Use long timeout client for OCR processing (10 minutes)
      const response = await apiClientLongTimeout.post(
        '/designiq/lists/upload_pid/',
        formData,
        {
          headers: {
            'Authorization': `Bearer ${token}`
            // Content-Type will be set automatically by axios for FormData
          },
          onUploadProgress: (progressEvent) => {
            const percentCompleted = Math.round((progressEvent.loaded * 100) / progressEvent.total);
            console.log('[P&ID Upload] ÃƒÂ°Ã…Â¸Ã¢â‚¬Å“Ã…Â  Upload progress:', percentCompleted + '%');
          }
        }
      );

      console.log('[P&ID Upload] ÃƒÂ¢Ã…â€œÃ¢â‚¬Â¦ Processing complete');
      
      const data = response.data;
      
      // Detect enriched vs base extraction
      const isEnriched = data.enriched_data && data.enriched_data.length > 0;
      const lines = isEnriched ? data.enriched_data : (data.extracted_lines || []);
      const columns = isEnriched && lines.length > 0 ? Object.keys(lines[0]) : null;
      
      setExtractedData({
        lines: lines,
        fileName: file.name,
        itemsCreated: data.items_created || 0,
        isEnriched: isEnriched,
        columns: columns
      });
      setShowPreviewModal(true);
      setUploadResult({
        success: true,
        message: isEnriched 
          ? `Successfully enriched ${lines.length} lines with ${columns?.length || 0} columns from ${file.name}`
          : `Successfully extracted ${lines.length} line numbers from ${file.name}`,
        data: data
      });
      setUploadingPID(false);
    } catch (error) {
      console.error('[P&ID Upload] ÃƒÂ¢Ã‚ÂÃ…â€™ Error:', error);
      
      let errorMessage = 'Failed to upload P&ID';
      
      if (error.code === 'ECONNABORTED') {
        errorMessage = 'Upload timed out. The PDF might be too large or complex. Please try a smaller file or contact support.';
      } else if (error.response) {
        // Server responded with error
        const errorData = error.response.data;
        errorMessage = errorData.detail || errorData.error || error.response.statusText || errorMessage;
      } else if (error.request) {
        // Request made but no response
        errorMessage = 'No response from server. Please check your connection and try again.';
      } else {
        errorMessage = error.message || errorMessage;
      }
      
      setUploadResult({
        success: false,
        message: errorMessage
      });
    } finally {
      setProcessing(false);
      setUploadingPID(false);
      event.target.value = '';
    }
  };

  const getStatusBadge = (status) => {
    const color = STATUS_COLORS[status] || 'gray';
    const colorClasses = {
      green: 'bg-green-100 text-green-800',
      yellow: 'bg-yellow-100 text-yellow-800',
      blue: 'bg-blue-100 text-blue-800',
      red: 'bg-red-100 text-red-800',
      gray: 'bg-gray-100 text-gray-800'
    };

    return (
      <span className={`px-2 py-1 text-xs font-medium rounded-full ${colorClasses[color]}`}>
        {status}
      </span>
    );
  };

  return (
    <div className="min-h-screen bg-gray-50 p-6">
      {/* Apply control styles for fullscreen and sidebar */}
      <style>{pageControls.styles}</style>

      {/* Header with Controls */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold text-gray-900">Engineering Lists</h1>
          <p className="mt-2 text-gray-600">Manage line lists, equipment, tie-ins, and alarms</p>
        </div>

        {/* Page Control Buttons */}
        <PageControlButtons
          sidebarVisible={pageControls.sidebarVisible}
          setSidebarVisible={pageControls.toggleSidebar}
          autoRefreshEnabled={pageControls.autoRefreshEnabled}
          setAutoRefreshEnabled={pageControls.toggleAutoRefresh}
          isFullscreen={pageControls.isFullscreen}
          toggleFullscreen={pageControls.toggleFullscreen}
          isRefreshing={isRefreshing}
          autoRefreshInterval={30}
        />
      </div>

      {/* List Type Tabs */}
      <div className="bg-white rounded-lg shadow-sm mb-6">
        <div className="border-b border-gray-200">
          <nav className="flex space-x-4 px-6" aria-label="Tabs">
            {Object.entries(LIST_TYPES).map(([key, config]) => {
              const Icon = config.icon;
              const isActive = selectedListType === key;
              
              return (
                <button
                  key={key}
                  onClick={() => setSelectedListType(key)}
                  className={`
                    flex items-center px-4 py-4 text-sm font-medium border-b-2 transition-colors
                    ${isActive
                      ? 'border-indigo-500 text-indigo-600'
                      : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                    }
                  `}
                >
                  <Icon className="w-5 h-5 mr-2" />
                  {config.name}
                </button>
              );
            })}
          </nav>
        </div>

        {/* Stats Cards */}
        {stats && (
          <div className="grid grid-cols-5 gap-6 p-6">
            <div className="text-center">
              <div className="text-3xl font-bold text-gray-900">{stats.total}</div>
              <div className="text-sm text-gray-600">Total Items</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-green-600">{stats.by_status.active}</div>
              <div className="text-sm text-gray-600">Active</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-yellow-600">{stats.by_status.pending}</div>
              <div className="text-sm text-gray-600">Pending</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-blue-600">{stats.by_status.approved}</div>
              <div className="text-sm text-gray-600">Approved</div>
            </div>
            <div className="text-center">
              <div className="text-3xl font-bold text-indigo-600">{stats.validated}</div>
              <div className="text-sm text-gray-600">Validated</div>
            </div>
          </div>
        )}
      </div>

      {/* Toolbar */}
      <div className="bg-white rounded-lg shadow-sm p-4 mb-6">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center space-x-4 flex-1">
            {/* Search */}
            <div className="flex-1 max-w-lg">
              <div className="relative">
                <input
                  type="text"
                  placeholder="Search by tag or description..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  onKeyPress={(e) => e.key === 'Enter' && handleSearch()}
                  className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                />
                <MagnifyingGlassIcon className="absolute left-3 top-2.5 h-5 w-5 text-gray-400" />
              </div>
            </div>

            {/* Filter Button */}
            <button
              onClick={() => setShowFilters(!showFilters)}
              className="flex items-center px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50"
            >
              <FunnelIcon className="w-5 h-5 mr-2" />
              Filters
            </button>
          </div>
        </div>
        
        {/* P&ID Upload Notice - Full Width Banner */}
        {selectedListType === 'line_list' && (
          <div className="w-full bg-gradient-to-r from-indigo-50 via-purple-50 to-pink-50 border-2 border-indigo-300 rounded-xl p-4">
            <div className="flex items-center space-x-3">
              <svg className="w-8 h-8 text-indigo-600 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
              </svg>
              <div className="flex-1">
                <h3 className="text-lg font-bold text-indigo-900 mb-1">📄 P&ID Processing Interface</h3>
                <p className="text-sm text-indigo-700">
                  Use the <strong>4-document enriched extraction interface below</strong> to upload and process P&ID files with full 34-column enrichment (P&ID + HMB + PMS + NACE)
                </p>
              </div>
              <div className="flex-shrink-0">
                <span className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-semibold">
                  Scroll Down ↓
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
      
      {/* 4-DOCUMENT ENRICHED EXTRACTION - Systematic Upload Interface */}
      {selectedListType === 'line_list' && (
      <div className="border-t-2 border-purple-200 pt-4 mt-4">
            <div className="bg-gradient-to-r from-purple-50 to-indigo-50 border-2 border-purple-300 rounded-xl p-4 mb-4">
              <div className="flex items-center space-x-3 mb-2">
                <svg className="w-6 h-6 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
                <h3 className="text-lg font-bold text-purple-900">Smart Enriched Extraction (34 Columns)</h3>
              </div>
              <p className="text-sm text-purple-800 mb-2">
                Upload <strong>P&ID + 3 Technical Documents</strong> to get full enriched table with 34 columns (8 base + 26 enriched)
              </p>
              <div className="flex items-center space-x-2 text-xs text-purple-700">
                <span className="font-semibold">Output:</span>
                <span className="px-2 py-1 bg-blue-100 text-blue-700 rounded">8 Base Columns from P&ID</span>
                <span className="text-purple-600">+</span>
                <span className="px-2 py-1 bg-green-100 text-green-700 rounded">10 from HMB</span>
                <span className="text-purple-600">+</span>
                <span className="px-2 py-1 bg-orange-100 text-orange-700 rounded">8 from PMS</span>
                <span className="text-purple-600">+</span>
                <span className="px-2 py-1 bg-red-100 text-red-700 rounded">8 from NACE</span>
                <span className="text-purple-600">=</span>
                <span className="px-2 py-1 bg-purple-100 text-purple-700 rounded font-bold">34 Total Columns</span>
              </div>
            </div>

            {/* FORMAT SELECTION - CRITICAL FOR REGEX PATTERNS */}
            <div className="bg-yellow-50 border-2 border-yellow-400 rounded-xl p-4 mb-4">
              <div className="flex items-center space-x-2 mb-3">
                <svg className="w-5 h-5 text-yellow-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
                <h4 className="text-sm font-bold text-yellow-900">Step 1: Select Project Format (Required)</h4>
              </div>
              <p className="text-xs text-yellow-800 mb-3">Choose your project type - this determines the line number format and regex patterns used for extraction</p>
              <div className="grid grid-cols-3 gap-3">
                <button
                  onClick={() => setSelectedFormat('onshore')}
                  className={`px-4 py-3 rounded-lg border-2 transition-all ${
                    selectedFormat === 'onshore'
                      ? 'bg-blue-500 text-white border-blue-600 shadow-lg'
                      : 'bg-white text-gray-700 border-gray-300 hover:border-blue-400'
                  }`}
                >
                  <div className="text-sm font-bold">Onshore</div>
                  <div className="text-xs mt-1">No area (2-D-5777-033842)</div>
                </button>
                <button
                  onClick={() => setSelectedFormat('general')}
                  className={`px-4 py-3 rounded-lg border-2 transition-all ${
                    selectedFormat === 'general'
                      ? 'bg-green-500 text-white border-green-600 shadow-lg'
                      : 'bg-white text-gray-700 border-gray-300 hover:border-green-400'
                  }`}
                >
                  <div className="text-sm font-bold">General</div>
                  <div className="text-xs mt-1">With area (1"-41-SWS-64544)</div>
                </button>
                <button
                  onClick={() => setSelectedFormat('offshore')}
                  className={`px-4 py-3 rounded-lg border-2 transition-all ${
                    selectedFormat === 'offshore'
                      ? 'bg-purple-500 text-white border-purple-600 shadow-lg'
                      : 'bg-white text-gray-700 border-gray-300 hover:border-purple-400'
                  }`}
                >
                  <div className="text-sm font-bold">Offshore</div>
                  <div className="text-xs mt-1">Area first (604-HO-8-BC2GA0)</div>
                </button>
              </div>
              {!selectedFormat && (
                <div className="mt-3 flex items-center space-x-2 text-red-600 text-sm font-medium">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
                  </svg>
                  <span>Please select a format before uploading documents</span>
                </div>
              )}
            </div>

            <div className="grid grid-cols-4 gap-4">
              {/* Document 1: P&ID (Mandatory) */}
              <div className="border-2 border-blue-300 rounded-lg p-4 bg-blue-50">
                <div className="flex items-center justify-between mb-2">
                  <label className="text-sm font-bold text-blue-900">1. P&ID Drawing</label>
                  <span className="px-2 py-1 bg-blue-600 text-white text-xs rounded-full">Mandatory</span>
                </div>
                <p className="text-xs text-blue-700 mb-3">Extracts: Line No, Size, Fluid Code, Area, From, To</p>
                <input
                  type="file"
                  accept=".pdf"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file && file.type === 'application/pdf') {
                      setPidDocument(file);
                      // Format must be selected explicitly by user before upload
                    } else {
                      alert('Please select a valid PDF file.');
                    }
                  }}
                  className="hidden"
                  id="pidFileInput"
                />
                <label
                  htmlFor="pidFileInput"
                  className="w-full flex items-center justify-center px-3 py-2 text-sm border-2 border-blue-400 text-blue-700 rounded-lg hover:bg-blue-100 transition-colors cursor-pointer"
                >
                  {pidDocument ? (
                    <>
                      <svg className="w-4 h-4 mr-2 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      <span className="truncate text-xs">{pidDocument.name}</span>
                    </>
                  ) : (
                    <>
                      <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M7 16a4 4 0 01-.88-7.903A5 5 0 1115.9 6L16 6a5 5 0 011 9.9M15 13l-3-3m0 0l-3 3m3-3v12" />
                      </svg>
                      Select P&ID
                    </>
                  )}
                </label>
                {pidDocument && (
                  <button
                    onClick={() => setPidDocument(null)}
                    className="w-full mt-2 text-xs text-red-600 hover:text-red-800 font-medium"
                  >
                    Remove
                  </button>
                )}
              </div>

              {/* Document 2: HMB/PFD */}
              <div className="border-2 border-green-300 rounded-lg p-4 bg-green-50">
                <div className="flex items-center justify-between mb-2">
                  <label className="text-sm font-bold text-green-900">2. HMB/PFD</label>
                  <span className="px-2 py-1 bg-green-600 text-white text-xs rounded-full">+10 cols</span>
                </div>
                <p className="text-xs text-green-700 mb-3">Design/Operating Temp, Pressure, Flow Rate, Density</p>
                <input
                  type="file"
                  ref={hmbRef}
                  accept=".pdf,.xlsx,.xls"
                  onChange={(e) => setHmbDocument(e.target.files?.[0] || null)}
                  className="hidden"
                />
                <button
                  onClick={() => hmbRef.current?.click()}
                  className="w-full flex items-center justify-center px-3 py-2 text-sm border-2 border-green-400 text-green-700 rounded-lg hover:bg-green-100 transition-colors"
                >
                  {hmbDocument ? (
                    <>
                      <svg className="w-4 h-4 mr-2 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      <span className="truncate text-xs">{hmbDocument.name}</span>
                    </>
                  ) : (
                    <>
                      <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                      </svg>
                      Select HMB
                    </>
                  )}
                </button>
                {hmbDocument && (
                  <button
                    onClick={() => setHmbDocument(null)}
                    className="w-full mt-2 text-xs text-red-600 hover:text-red-800 font-medium"
                  >
                    Remove
                  </button>
                )}
              </div>

              {/* Document 3: PMS */}
              <div className="border-2 border-orange-300 rounded-lg p-4 bg-orange-50">
                <div className="flex items-center justify-between mb-2">
                  <label className="text-sm font-bold text-orange-900">3. PMS</label>
                  <span className="px-2 py-1 bg-orange-600 text-white text-xs rounded-full">+8 cols</span>
                </div>
                <p className="text-xs text-orange-700 mb-3">Material Grade, Schedule, Flange Rating, Gaskets</p>
                <input
                  type="file"
                  ref={pmsRef}
                  accept=".pdf,.xlsx,.xls"
                  onChange={(e) => setPmsDocument(e.target.files?.[0] || null)}
                  className="hidden"
                />
                <button
                  onClick={() => pmsRef.current?.click()}
                  className="w-full flex items-center justify-center px-3 py-2 text-sm border-2 border-orange-400 text-orange-700 rounded-lg hover:bg-orange-100 transition-colors"
                >
                  {pmsDocument ? (
                    <>
                      <svg className="w-4 h-4 mr-2 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      <span className="truncate text-xs">{pmsDocument.name}</span>
                    </>
                  ) : (
                    <>
                      <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                      </svg>
                      Select PMS
                    </>
                  )}
                </button>
                {pmsDocument && (
                  <button
                    onClick={() => setPmsDocument(null)}
                    className="w-full mt-2 text-xs text-red-600 hover:text-red-800 font-medium"
                  >
                    Remove
                  </button>
                )}
              </div>

              {/* Document 4: NACE */}
              <div className="border-2 border-red-300 rounded-lg p-4 bg-red-50">
                <div className="flex items-center justify-between mb-2">
                  <label className="text-sm font-bold text-red-900">4. NACE Report</label>
                  <span className="px-2 py-1 bg-red-600 text-white text-xs rounded-full">+8 cols</span>
                </div>
                <p className="text-xs text-red-700 mb-3">Corrosion Allowance, NACE Class, H2S, Coating</p>
                <input
                  type="file"
                  ref={naceRef}
                  accept=".pdf,.xlsx,.xls"
                  onChange={(e) => setNaceDocument(e.target.files?.[0] || null)}
                  className="hidden"
                />
                <button
                  onClick={() => naceRef.current?.click()}
                  className="w-full flex items-center justify-center px-3 py-2 text-sm border-2 border-red-400 text-red-700 rounded-lg hover:bg-red-100 transition-colors"
                >
                  {naceDocument ? (
                    <>
                      <svg className="w-4 h-4 mr-2 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      <span className="truncate text-xs">{naceDocument.name}</span>
                    </>
                  ) : (
                    <>
                      <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                      </svg>
                      Select NACE
                    </>
                  )}
                </button>
                {naceDocument && (
                  <button
                    onClick={() => setNaceDocument(null)}
                    className="w-full mt-2 text-xs text-red-600 hover:text-red-800 font-medium"
                  >
                    Remove
                  </button>
                )}
              </div>

              {/* Document 5: Stress Criticality */}
              <div className="border-2 border-yellow-300 rounded-lg p-4 bg-yellow-50">
                <div className="flex items-center justify-between mb-2">
                  <label className="text-sm font-bold text-yellow-900">5. Stress Criticality</label>
                  <span className="px-2 py-1 bg-yellow-600 text-white text-xs rounded-full">+1 col</span>
                </div>
                <p className="text-xs text-yellow-700 mb-3">Section 7 + Temperature Analysis</p>
                <input
                  type="file"
                  ref={stressRef}
                  accept=".pdf,.xlsx,.xls"
                  onChange={(e) => setStressCriticalityDocument(e.target.files?.[0] || null)}
                  className="hidden"
                />
                <button
                  onClick={() => stressRef.current?.click()}
                  className="w-full flex items-center justify-center px-3 py-2 text-sm border-2 border-yellow-400 text-yellow-700 rounded-lg hover:bg-yellow-100 transition-colors"
                >
                  {stressCriticalityDocument ? (
                    <>
                      <svg className="w-4 h-4 mr-2 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                      </svg>
                      <span className="truncate text-xs">{stressCriticalityDocument.name}</span>
                    </>
                  ) : (
                    <>
                      <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                      </svg>
                      Select Stress Document
                    </>
                  )}
                </button>
                {stressCriticalityDocument && (
                  <button
                    onClick={() => setStressCriticalityDocument(null)}
                    className="w-full mt-2 text-xs text-yellow-600 hover:text-yellow-800 font-medium"
                  >
                    Remove
                  </button>
                )}
              </div>
            </div>

            {/* Status Indicator & Process Button */}
            <div className="mt-4 p-4 bg-gradient-to-r from-gray-50 to-blue-50 border-2 border-gray-300 rounded-lg">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-4">
                  <div className={`w-4 h-4 rounded-full ${
                    pidDocument && hmbDocument && pmsDocument && naceDocument && stressCriticalityDocument
                      ? 'bg-green-500 animate-pulse' 
                      : 'bg-gray-300'
                  }`}></div>
                  <div>
                    <span className="text-sm font-medium text-gray-700">
                      Documents: <strong className="text-lg">{[pidDocument, hmbDocument, pmsDocument, naceDocument, stressCriticalityDocument].filter(Boolean).length}/5</strong> uploaded
                    </span>
                    <div className="flex items-center space-x-2 mt-1">
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        pidDocument ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-500'
                      }`}>P&ID</span>
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        hmbDocument ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
                      }`}>HMB</span>
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        pmsDocument ? 'bg-orange-100 text-orange-700' : 'bg-gray-100 text-gray-500'
                      }`}>PMS</span>
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        naceDocument ? 'bg-red-100 text-red-700' : 'bg-gray-100 text-gray-500'
                      }`}>NACE</span>
                      <span className={`text-xs px-2 py-0.5 rounded ${
                        stressCriticalityDocument ? 'bg-yellow-100 text-yellow-700' : 'bg-gray-100 text-gray-500'
                      }`}>STRESS</span>
                    </div>
                  </div>
                </div>
                <button
                  onClick={processAllDocuments}
                  disabled={!pidDocument || !hmbDocument || !pmsDocument || !naceDocument || !stressCriticalityDocument || loading}
                  className={`px-6 py-3 rounded-lg font-bold text-white transition-all transform hover:scale-105 ${
                    pidDocument && hmbDocument && pmsDocument && naceDocument && stressCriticalityDocument && !loading
                      ? 'bg-gradient-to-r from-purple-600 to-indigo-600 hover:from-purple-700 hover:to-indigo-700 shadow-lg animate-pulse'
                      : 'bg-gray-300 cursor-not-allowed'
                  }`}
                >
                  {loading ? (
                    <div className="flex items-center space-x-2">
                      <svg className="animate-spin h-5 w-5" fill="none" viewBox="0 0 24 24">
                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                      </svg>
                      <span>Processing...</span>
                    </div>
                  ) : (
                    <div className="flex items-center space-x-2">
                      <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                      </svg>
                      <span>Process All 5 Documents</span>
                    </div>
                  )}
                </button>
              </div>
              {pidDocument && hmbDocument && pmsDocument && naceDocument && stressCriticalityDocument && !loading && (
                <div className="mt-3 flex items-center justify-center text-sm text-green-600 font-semibold">
                  <svg className="w-5 h-5 mr-2" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm3.707-9.293a1 1 0 00-1.414-1.414L9 10.586 7.707 9.293a1 1 0 00-1.414 1.414l2 2a1 1 0 001.414 0l4-4z" clipRule="evenodd" />
                  </svg>
                  Ready! Click "Process All 5 Documents" to extract 35 columns (8 base from P&ID + 27 enriched via AI)
                </div>
              )}
              {(!pidDocument || !hmbDocument || !pmsDocument || !naceDocument || !stressCriticalityDocument) && (
                <div className="mt-3 text-center text-sm text-yellow-700 font-medium">
                  ⚠️ Upload all 5 documents to enable processing with 35-column enrichment
                </div>
              )}
            </div>
          </div>
          )}

        {/* Filter Panel */}
        {showFilters && (
          <div className="mt-4 pt-4 border-t border-gray-200">
            <div className="flex items-center space-x-4">
              <label className="text-sm font-medium text-gray-700">Status:</label>
              <select
                value={statusFilter}
                onChange={(e) => setStatusFilter(e.target.value)}
                className="px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500"
              >
                <option value="all">All Status</option>
                <option value="active">Active</option>
                <option value="pending">Pending</option>
                <option value="approved">Approved</option>
                <option value="rejected">Rejected</option>
                <option value="inactive">Inactive</option>
              </select>
            </div>
          </div>
        )}

      {/* Upload Result Notification */}
      {uploadResult && (
        <div className="fixed bottom-8 right-8 z-50 animate-slide-up">
          <div className={`rounded-xl shadow-2xl p-6 max-w-md ${
            uploadResult.success 
              ? 'bg-gradient-to-r from-green-50 to-emerald-50 border-2 border-green-200'
              : 'bg-gradient-to-r from-red-50 to-pink-50 border-2 border-red-200'
          }`}>
            <div className="flex items-start">
              <div className={`flex-shrink-0 ${
                uploadResult.success ? 'text-green-600' : 'text-red-600'
              }`}>
                {uploadResult.success ? (
                  <CheckCircleIcon className="h-8 w-8" />
                ) : (
                  <XCircleIcon className="h-8 w-8" />
                )}
              </div>
              <div className="ml-4 flex-1">
                <h3 className={`text-lg font-semibold ${
                  uploadResult.success ? 'text-green-900' : 'text-red-900'
                }`}>
                  {uploadResult.success ? 'Upload Successful!' : 'Upload Failed'}
                </h3>
                <p className={`mt-2 text-sm ${
                  uploadResult.success ? 'text-green-700' : 'text-red-700'
                }`}>
                  {uploadResult.message}
                </p>
                {uploadResult.success && uploadResult.data?.extracted_lines && (
                  <div className="mt-3 text-xs text-green-600 font-medium">
                    <p>ÃƒÂ¢Ã…â€œÃ¢â‚¬Å“ OCR processing completed</p>
                    <p>ÃƒÂ¢Ã…â€œÃ¢â‚¬Å“ {uploadResult.data.extracted_lines.length} line numbers detected</p>
                  </div>
                )}
              </div>
              <button
                onClick={() => setUploadResult(null)}
                className="ml-4 text-gray-400 hover:text-gray-600"
              >
                <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 20 20">
                  <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                </svg>
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Processing Overlay */}
      {processing && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
          <div className="bg-white rounded-2xl p-8 max-w-md shadow-2xl">
            <div className="flex flex-col items-center">
              <div className="animate-spin rounded-full h-16 w-16 border-4 border-blue-600 border-t-transparent mb-4"></div>
              <h3 className="text-xl font-bold text-gray-900 mb-2">Processing P&ID Document</h3>
              <p className="text-gray-600 text-center mb-4">
                Using Multi-Engine OCR + AI to extract line numbers...
              </p>
              <div className="w-full bg-gray-200 rounded-full h-2 overflow-hidden mb-3">
                <div className="bg-gradient-to-r from-blue-500 to-purple-500 h-full animate-pulse" style={{ width: '70%' }}></div>
              </div>
              <div className="space-y-2 text-sm text-gray-600 w-full">
                <div className="flex items-center">
                  <div className="w-2 h-2 bg-blue-500 rounded-full mr-2 animate-pulse"></div>
                  <span>Tesseract OCR (Horizontal text)</span>
                </div>
                <div className="flex items-center">
                  <div className="w-2 h-2 bg-purple-500 rounded-full mr-2 animate-pulse"></div>
                  <span>EasyOCR (Vertical text detection)</span>
                </div>
                <div className="flex items-center">
                  <div className="w-2 h-2 bg-indigo-500 rounded-full mr-2 animate-pulse"></div>
                  <span>PaddleOCR (Multi-orientation)</span>
                </div>
                <div className="flex items-center">
                  <div className="w-2 h-2 bg-green-500 rounded-full mr-2 animate-pulse"></div>
                  <span>OpenAI GPT-4 (Smart parsing)</span>
                </div>
              </div>
              <p className="text-sm text-gray-500 mt-4 text-center">
                ÃƒÂ¢Ã‚ÂÃ‚Â±ÃƒÂ¯Ã‚Â¸Ã‚Â <strong>Processing time:</strong> 2-10 minutes for complex PDFs
                <br />
                <span className="text-xs">Please keep this window open</span>
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Processing Modal with Progress */}
      {showProcessingModal && (
        <div className="fixed inset-0 bg-black bg-opacity-75 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-2xl w-full p-8">
            <div className="text-center">
              <div className="mb-6">
                <svg className="animate-spin h-16 w-16 mx-auto text-purple-600" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                </svg>
              </div>
              
              <h2 className="text-2xl font-bold text-gray-900 mb-2">Processing 4 Documents</h2>
              <p className="text-gray-600 mb-6">
                P&ID + HMB + PMS + NACE → 34-column enriched table
              </p>
              
              {/* Progress Bar */}
              <div className="mb-4">
                <div className="bg-gray-200 rounded-full h-4 overflow-hidden">
                  <div 
                    className="bg-gradient-to-r from-purple-600 to-indigo-600 h-full transition-all duration-500 ease-out"
                    style={{ width: `${processingProgress.percent}%` }}
                  ></div>
                </div>
                <p className="text-sm text-gray-700 mt-2 font-semibold">{processingProgress.percent}%</p>
              </div>
              
              {/* Current Step */}
              <div className="bg-blue-50 border-2 border-blue-200 rounded-lg p-4 mt-4">
                <p className="text-sm text-blue-900 font-medium">{processingProgress.step}</p>
              </div>
              
              {/* Info Box */}
              <div className="mt-6 p-4 bg-purple-50 border border-purple-200 rounded-lg text-left">
                <h3 className="font-semibold text-purple-900 mb-2 flex items-center">
                  <svg className="w-5 h-5 mr-2" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
                  </svg>
                  What's Happening?
                </h3>
                <ul className="text-sm text-purple-800 space-y-1">
                  <li>• <strong>Step 1:</strong> Extract 8 base columns from P&ID (locked logic)</li>
                  <li>• <strong>Step 2:</strong> AI analyzes 3 documents for +26 enrichment columns</li>
                  <li>• <strong>Step 3:</strong> Merge into guaranteed 34-column table</li>
                </ul>
              </div>
              
              <p className="text-xs text-gray-500 mt-4">
                This may take 1-3 minutes depending on file sizes and AI processing time
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Preview Modal */}
      {showPreviewModal && extractedData && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-6xl w-full max-h-[90vh] overflow-hidden flex flex-col">
            {/* Modal Header */}
            <div className="bg-gradient-to-r from-blue-600 to-purple-600 text-white px-6 py-4 flex justify-between items-center">
              <div>
                <h2 className="text-2xl font-bold">P&ID Line Numbers Extracted</h2>
                <p className="text-blue-100 text-sm mt-1">{extractedData.fileName}</p>
              </div>
              <button
                onClick={() => {
                  setShowPreviewModal(false);
                  fetchData();
                  setUploadResult(null);
                }}
                className="text-white hover:bg-white hover:bg-opacity-20 rounded-lg p-2 transition-colors"
              >
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>

            {/* Summary Stats */}
            <div className="px-6 py-4 bg-gradient-to-r from-green-50 to-emerald-50 border-b border-green-200">
              <div className="flex items-center justify-between">
                <div className="flex items-center space-x-6">
                  <div className="flex items-center space-x-2">
                    <CheckCircleIcon className="w-6 h-6 text-green-600" />
                    <div>
                      <p className="text-sm text-gray-600">Lines Detected</p>
                      <p className="text-2xl font-bold text-gray-900">{extractedData.lines.length}</p>
                    </div>
                  </div>
                  <div className="flex items-center space-x-2">
                    <svg className="w-6 h-6 text-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                    </svg>
                    <div>
                      <p className="text-sm text-gray-600">Items Created</p>
                      <p className="text-2xl font-bold text-gray-900">{extractedData.itemsCreated}</p>
                    </div>
                  </div>
                  <div className="flex items-center space-x-2">
                    <svg className="w-6 h-6 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 10V3L4 14h7v7l9-11h-7z" />
                    </svg>
                    <div>
                      <p className="text-sm text-gray-600">Total Columns</p>
                      <p className="text-2xl font-bold text-gray-900">35 <span className="text-sm text-gray-600">(9 base + 26 enriched)</span></p>
                    </div>
                  </div>
                </div>
                <button
                  onClick={() => {
                    const data = extractedData.lines;
                    
                    // ALL 35 COLUMNS Excel Export (From and To separate)
                    const headers = [
                      'Line Number', 'Size', 'Fluid Code', 'Area', 'Sequence No', 'PIPR Class', 'Insulation', 'From', 'To',
                      'Flow Medium', 'Two Phase', 'Surge Flow', 'Flow Max', 'Density', 
                      'Normal Pressure', 'Normal Temp', 'Design Pressure', 'Minimax Design Temp',
                      'Design Code', 'Category-M Fluid', 'Schedule / Wall THK', 'Stress Relief', 'PWHT',
                      'RT', 'MT/PT', 'Hardness', 'Visual', 'NACE-MR-0175', 'Piping Rated Pressure',
                      'Test Pressure', 'Test Medium', 'P&ID No.', 'P&ID Rev', 'Date', 'Criticality Code'
                    ];
                    
                    const wsData = [headers];
                    
                    // Add all 35 columns for each row
                    data.forEach(row => {
                      wsData.push([
                        row.original_detection || row.line_number || '',
                        row.size || '',
                        row.fluid_code || '',
                        row.area || '',
                        row.sequence_no || '',
                        row.pipr_class || '',
                        row.insulation || '',
                        row.from_line || row.from || '',
                        row.to_line || row.to || '',
                        row.flow_medium || '',
                        row.two_phase || '',
                        row.surge_flow || '',
                        row.flow_max || '',
                        row.density || '',
                        row.normal_pressure || '',
                        row.normal_temp || '',
                        row.design_pressure || '',
                        row.minimax_design_temp || '',
                        row.design_code || '',
                        row.category_m_fluid || '',
                        row.schedule_wall_thk || '',
                        row.stress_relief || '',
                        row.pwht || '',
                        row.rt || '',
                        row.mt_pt || '',
                        row.hardness || '',
                        row.visual || '',
                        row.nace_mr_0175 || '',
                        row.piping_rated_pressure || '',
                        row.test_pressure || '',
                        row.test_medium || '',
                        row.pid_no || '',
                        row.pid_rev || '',
                        row.date || '',
                        row.criticality_code || ''
                      ]);
                    });
                    
                    const wb = XLSX.utils.book_new();
                    const ws = XLSX.utils.aoa_to_sheet(wsData);
                    
                    // Set column widths for all 35 columns
                    ws['!cols'] = [
                      { wch: 20 }, { wch: 8 }, { wch: 12 }, { wch: 10 }, { wch: 15 }, { wch: 15 }, { wch: 12 }, { wch: 20 }, { wch: 20 },
                      { wch: 15 }, { wch: 10 }, { wch: 12 }, { wch: 12 }, { wch: 10 },
                      { wch: 15 }, { wch: 12 }, { wch: 15 }, { wch: 18 },
                      { wch: 15 }, { wch: 16 }, { wch: 18 }, { wch: 12 }, { wch: 10 },
                      { wch: 8 }, { wch: 10 }, { wch: 10 }, { wch: 10 }, { wch: 15 }, { wch: 20 },
                      { wch: 15 }, { wch: 15 }, { wch: 15 }, { wch: 12 }, { wch: 12 }, { wch: 15 }
                    ];
                    
                    XLSX.utils.book_append_sheet(wb, ws, 'P&ID 35 Columns');
                    
                    const timestamp = new Date().toISOString().split('T')[0];
                    XLSX.writeFile(wb, `PID_35Columns_${data.length}lines_${timestamp}.xlsx`);
                  }}
                  className="flex items-center px-6 py-3 bg-gradient-to-r from-green-600 to-emerald-600 text-white rounded-xl hover:shadow-lg transition-all font-semibold"
                >
                  <svg className="w-5 h-5 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                  </svg>
                  Download Excel
                </button>
              </div>
            </div>

            {/* Table Preview - ALL 35 COLUMNS */}
            <div className="flex-1 overflow-auto px-6 py-4">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gradient-to-r from-indigo-600 to-purple-600 sticky top-0">
                  <tr>
                    {/* 8 BASE COLUMNS from P&ID (STEP 1) */}
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider whitespace-nowrap">Line Number</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider whitespace-nowrap">Size</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider whitespace-nowrap">Fluid Code</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider whitespace-nowrap">Area</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider whitespace-nowrap">Sequence No</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider whitespace-nowrap">PIPR Class</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider whitespace-nowrap">Insulation</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider whitespace-nowrap">From</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider whitespace-nowrap">To</th>
                    
                    {/* 27 ENRICHED COLUMNS from HMB/PMS/NACE + Stress (AI-extracted) */}
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Flow Medium</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Two Phase</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Surge Flow</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Flow Max</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Density</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Normal Pressure</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Normal Temp</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Design Pressure</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Minimax Design Temp</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Design Code</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Category-M Fluid</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Schedule / Wall THK</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Stress Relief</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">PWHT</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">RT</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">MT/PT</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Hardness</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Visual</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">NACE-MR-0175</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Piping Rated Pressure</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Test Pressure</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Test Medium</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">P&ID No.</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">P&ID Rev</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Date</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Criticality Code</th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-yellow-200 uppercase tracking-wider whitespace-nowrap bg-purple-700">Criticality Stress</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {extractedData.lines.map((line, index) => (
                    <tr key={index} className="hover:bg-gray-50 transition-colors">
                      {/* 8 BASE COLUMNS from P&ID (white background) */}
                      <td className="px-4 py-3 whitespace-nowrap text-sm font-bold text-indigo-600">
                        {line.original_detection || line.line_number || '-'}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700">
                        {line.size || '-'}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900">
                        {line.fluid_code || '-'}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 font-semibold text-blue-600">
                        {line.area || '-'}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700">
                        {line.sequence_no || '-'}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700">
                        {line.pipr_class || '-'}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700">
                        {line.insulation || '-'}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700">
                        {line.from_line || line.from || '-'}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700">
                        {line.to_line || line.to || '-'}
                      </td>
                      
                      {/* 27 ENRICHED COLUMNS from HMB/PMS/NACE + Stress (AI-extracted with OpenAI) */}
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.flow_medium || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.two_phase || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.surge_flow || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.flow_max || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.density || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.normal_pressure || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.normal_temp || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.design_pressure || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.minimax_design_temp || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.design_code || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.category_m_fluid || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.schedule_wall_thk || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.stress_relief || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.pwht || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.rt || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.mt_pt || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.hardness || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.visual || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.nace_mr_0175 || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.piping_rated_pressure || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.test_pressure || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.test_medium || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.pid_no || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.pid_rev || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.date || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.criticality_code || '-'}</td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700 bg-yellow-50">{line.criticality_stress || '-'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Modal Footer */}
            <div className="px-6 py-4 bg-gray-50 border-t border-gray-200 flex justify-end space-x-3">
              <button
                onClick={() => {
                  setShowPreviewModal(false);
                  fetchData();
                  setUploadResult(null);
                }}
                className="px-6 py-3 border-2 border-gray-300 text-gray-700 rounded-xl hover:bg-gray-100 transition-colors font-semibold"
              >
                Close & Refresh
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Previous Outputs Section - Historical Downloads */}
      {selectedListType === 'line_list' && (
        <div className="mt-8 bg-white rounded-xl shadow-sm border-2 border-gray-200 overflow-hidden">
          {/* Section Header */}
          <div className="bg-gradient-to-r from-indigo-50 to-purple-50 px-6 py-4 border-b-2 border-indigo-200">
            <div className="flex items-center justify-between">
              <div className="flex items-center space-x-3">
                <svg className="w-6 h-6 text-indigo-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 10v6m0 0l-3-3m3 3l3-3m2 8H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <div>
                  <h3 className="text-lg font-bold text-indigo-900">📂 Previous Outputs</h3>
                  <p className="text-sm text-indigo-700">Download previously processed P&ID Excel files</p>
                </div>
              </div>
              <button
                onClick={fetchPreviousOutputs}
                disabled={loadingOutputs}
                className="flex items-center px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors disabled:opacity-50"
              >
                <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
                </svg>
                Refresh
              </button>
            </div>
          </div>

          {/* Table Content */}
          <div className="overflow-x-auto">
            {loadingOutputs ? (
              <div className="flex items-center justify-center py-12">
                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"></div>
                <span className="ml-3 text-gray-600">Loading previous outputs...</span>
              </div>
            ) : previousOutputs.length === 0 ? (
              <div className="text-center py-12">
                <svg className="w-16 h-16 mx-auto text-gray-400 mb-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                <p className="text-gray-600 font-medium">No previous outputs found</p>
                <p className="text-gray-500 text-sm mt-1">Upload and process a P&ID to see outputs here</p>
              </div>
            ) : (
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">P&ID No</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Rev</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Date</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Lines</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Columns</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Size</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Enriched</th>
                    <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Action</th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {previousOutputs.map((output) => (
                    <tr key={output.id} className="hover:bg-gray-50 transition-colors">
                      <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                        {output.pid_number}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">
                        {output.pid_revision || '-'}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">
                        {output.processing_date}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">
                        <span className="px-2 py-1 bg-blue-100 text-blue-700 rounded-full text-xs font-semibold">
                          {output.total_lines} lines
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">
                        <span className="px-2 py-1 bg-purple-100 text-purple-700 rounded-full text-xs font-semibold">
                          {output.total_columns} cols
                        </span>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-600">
                        {output.file_size_mb} MB
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm">
                        {output.enrichment_enabled ? (
                          <span className="px-2 py-1 bg-green-100 text-green-700 rounded-full text-xs font-semibold">
                            ✓ Yes
                          </span>
                        ) : (
                          <span className="px-2 py-1 bg-gray-100 text-gray-700 rounded-full text-xs">
                            Base Only
                          </span>
                        )}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm">
                        <button
                          onClick={() => handleDownloadOutput(output.id, output.excel_filename)}
                          className="flex items-center px-3 py-1.5 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 transition-colors text-xs font-semibold"
                        >
                          <ArrowDownTrayIcon className="w-4 h-4 mr-1" />
                          Download
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}

      {/* Line Number Format Configuration Modal */}
      {showFormatConfigModal && (
        <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-2xl shadow-2xl max-w-4xl w-full max-h-[90vh] overflow-hidden flex flex-col">
            {/* Modal Header */}
            <div className="bg-gradient-to-r from-purple-600 to-indigo-600 px-6 py-4">
              <h2 className="text-2xl font-bold text-white">Custom Line Number Format</h2>
              <p className="text-purple-100 text-sm mt-1">
                Configure components and order for your specific P&ID format
              </p>
            </div>

            {/* Modal Body */}
            <div className="flex-1 overflow-auto p-6 space-y-6">
              
              {/* Format Template Preview */}
              <div className="bg-gradient-to-br from-indigo-50 to-purple-50 border-2 border-indigo-200 rounded-xl p-4">
                <h3 className="text-sm font-semibold text-gray-700 mb-2">📋 Current Format Template</h3>
                <div className="bg-white px-4 py-3 rounded-lg border border-indigo-300 font-mono text-lg">
                  {lineNumberFormat.components
                    .filter(c => c.enabled)
                    .sort((a, b) => a.order - b.order)
                    .map(c => c.id.toUpperCase())
                    .join(lineNumberFormat.separator) || 'No components selected'}
                </div>
                <div className="mt-2 text-sm text-gray-600">
                  Example: {lineNumberFormat.components
                    .filter(c => c.enabled)
                    .sort((a, b) => a.order - b.order)
                    .map(c => c.example)
                    .join(lineNumberFormat.separator) || 'Configure components below'}
                </div>
              </div>

              {/* Separator Configuration */}
              <div className="bg-white border border-gray-200 rounded-xl p-4">
                <label className="block text-sm font-semibold text-gray-700 mb-3">
                  🔗 Component Separator
                </label>
                <div className="flex items-center space-x-4">
                  <input
                    type="text"
                    value={lineNumberFormat.separator}
                    onChange={(e) => setLineNumberFormat({
                      ...lineNumberFormat,
                      separator: e.target.value
                    })}
                    maxLength={3}
                    className="w-24 px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-purple-500 focus:border-transparent text-center font-mono text-lg"
                    placeholder="-"
                  />
                  <label className="flex items-center space-x-2">
                    <input
                      type="checkbox"
                      checked={lineNumberFormat.allowVariableSeparators}
                      onChange={(e) => setLineNumberFormat({
                        ...lineNumberFormat,
                        allowVariableSeparators: e.target.checked
                      })}
                      className="w-4 h-4 text-purple-600 border-gray-300 rounded focus:ring-purple-500"
                    />
                    <span className="text-sm text-gray-700">Allow variable separators (-, –, —, etc.)</span>
                  </label>
                </div>
              </div>

              {/* Components Configuration */}
              <div className="bg-white border border-gray-200 rounded-xl p-4">
                <h3 className="text-sm font-semibold text-gray-700 mb-4">⚙️ Line Number Components</h3>
                <div className="space-y-3">
                  {lineNumberFormat.components.map((component, idx) => (
                    <div key={component.id} className="flex items-center space-x-4 p-3 bg-gray-50 rounded-lg border border-gray-200">
                      {/* Enable Checkbox */}
                      <input
                        type="checkbox"
                        checked={component.enabled}
                        onChange={(e) => {
                          const updated = [...lineNumberFormat.components];
                          updated[idx].enabled = e.target.checked;
                          setLineNumberFormat({ ...lineNumberFormat, components: updated });
                        }}
                        className="w-5 h-5 text-purple-600 border-gray-300 rounded focus:ring-purple-500"
                      />
                      
                      {/* Component Name */}
                      <div className="flex-1 min-w-[150px]">
                        <span className={`font-semibold ${component.enabled ? 'text-gray-900' : 'text-gray-400'}`}>
                          {component.name}
                        </span>
                      </div>

                      {/* Order */}
                      <div className="flex items-center space-x-2">
                        <label className="text-sm text-gray-600 whitespace-nowrap">Order:</label>
                        <input
                          type="number"
                          min="1"
                          max="6"
                          value={component.order}
                          onChange={(e) => {
                            const updated = [...lineNumberFormat.components];
                            updated[idx].order = parseInt(e.target.value) || 1;
                            setLineNumberFormat({ ...lineNumberFormat, components: updated });
                          }}
                          disabled={!component.enabled}
                          className={`w-16 px-2 py-1 border rounded-lg text-center ${
                            component.enabled 
                              ? 'border-gray-300 focus:ring-2 focus:ring-purple-500' 
                              : 'bg-gray-100 border-gray-200 text-gray-400'
                          }`}
                        />
                      </div>

                      {/* Pattern */}
                      <div className="flex items-center space-x-2 flex-1 min-w-[200px]">
                        <label className="text-sm text-gray-600 whitespace-nowrap">Regex:</label>
                        <input
                          type="text"
                          value={component.pattern}
                          onChange={(e) => {
                            const updated = [...lineNumberFormat.components];
                            updated[idx].pattern = e.target.value;
                            setLineNumberFormat({ ...lineNumberFormat, components: updated });
                          }}
                          disabled={!component.enabled}
                          className={`flex-1 px-3 py-1 border rounded-lg font-mono text-sm ${
                            component.enabled 
                              ? 'border-gray-300 focus:ring-2 focus:ring-purple-500' 
                              : 'bg-gray-100 border-gray-200 text-gray-400'
                          }`}
                          placeholder="\\d{1,2}"
                        />
                      </div>

                      {/* Example */}
                      <div className="flex items-center space-x-2 min-w-[100px]">
                        <label className="text-sm text-gray-600">Ex:</label>
                        <input
                          type="text"
                          value={component.example}
                          onChange={(e) => {
                            const updated = [...lineNumberFormat.components];
                            updated[idx].example = e.target.value;
                            setLineNumberFormat({ ...lineNumberFormat, components: updated });
                          }}
                          disabled={!component.enabled}
                          className={`w-20 px-2 py-1 border rounded-lg text-sm ${
                            component.enabled 
                              ? 'border-gray-300 focus:ring-2 focus:ring-purple-500' 
                              : 'bg-gray-100 border-gray-200 text-gray-400'
                          }`}
                          placeholder="2"
                        />
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* Preset Templates */}
              <div className="bg-white border border-gray-200 rounded-xl p-4">
                <h3 className="text-sm font-semibold text-gray-700 mb-3">🎯 Common Configurations</h3>
                <div className="grid grid-cols-2 gap-3">
                  <button
                    onClick={() => {
                      setLineNumberFormat({
                        ...lineNumberFormat,
                        components: [
                          { id: 'line_size', name: 'Line Size', enabled: true, order: 1, pattern: '\\d{1,2}', example: '36' },
                          { id: 'area', name: 'Area', enabled: false, order: 2, pattern: '\\d{2,3}', example: '41' },
                          { id: 'fluid_code', name: 'Fluid Code', enabled: true, order: 2, pattern: '[A-Z]{1,3}', example: 'SWR' },
                          { id: 'sequence_no', name: 'Sequence No', enabled: true, order: 3, pattern: '\\d{3,5}', example: '60302' },
                          { id: 'pipe_class', name: 'Pipe Class', enabled: true, order: 4, pattern: '\\d{3,5}', example: '5010' },
                          { id: 'insulation', name: 'Insulation', enabled: false, order: 5, pattern: '[A-Z]{1,2}', example: 'V' }
                        ]
                      });
                    }}
                    className="px-4 py-3 bg-blue-50 border-2 border-blue-200 rounded-lg hover:bg-blue-100 text-left"
                  >
                    <div className="font-semibold text-blue-700">Standard Format</div>
                    <div className="text-xs text-blue-600 font-mono mt-1">SIZE-FLUID-SEQ-CLASS</div>
                    <div className="text-xs text-gray-600 mt-1">Example: 2-PU-152-50100A</div>
                  </button>
                  
                  <button
                    onClick={() => {
                      setLineNumberFormat({
                        ...lineNumberFormat,
                        components: [
                          { id: 'line_size', name: 'Line Size', enabled: true, order: 1, pattern: '\\d{1,2}', example: '36' },
                          { id: 'area', name: 'Area', enabled: true, order: 2, pattern: '\\d{2,3}', example: '41' },
                          { id: 'fluid_code', name: 'Fluid Code', enabled: true, order: 3, pattern: '[A-Z]{1,3}', example: 'SWR' },
                          { id: 'sequence_no', name: 'Sequence No', enabled: true, order: 4, pattern: '\\d{3,5}', example: '60302' },
                          { id: 'pipe_class', name: 'Pipe Class', enabled: true, order: 5, pattern: '\\d{3,5}', example: '5010' },
                          { id: 'insulation', name: 'Insulation', enabled: true, order: 6, pattern: '[A-Z]{1,2}', example: 'V' }
                        ]
                      });
                    }}
                    className="px-4 py-3 bg-green-50 border-2 border-green-200 rounded-lg hover:bg-green-100 text-left"
                  >
                    <div className="font-semibold text-green-700">Extended Format</div>
                    <div className="text-xs text-green-600 font-mono mt-1">SIZE-AREA-FLUID-SEQ-CLASS-INS</div>
                    <div className="text-xs text-gray-600 mt-1">Example: 2-41-PU-152-50100A-X</div>
                  </button>
                </div>
              </div>

              {/* Help Text */}
              <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4">
                <div className="flex items-start space-x-3">
                  <svg className="w-5 h-5 text-yellow-600 mt-0.5" fill="currentColor" viewBox="0 0 20 20">
                    <path fillRule="evenodd" d="M18 10a8 8 0 11-16 0 8 8 0 0116 0zm-7-4a1 1 0 11-2 0 1 1 0 012 0zM9 9a1 1 0 000 2v3a1 1 0 001 1h1a1 1 0 100-2v-3a1 1 0 00-1-1H9z" clipRule="evenodd" />
                  </svg>
                  <div className="text-sm text-yellow-800">
                    <p className="font-semibold mb-1">How to use:</p>
                    <ul className="list-disc list-inside space-y-1 text-xs">
                      <li>Enable/disable components using checkboxes</li>
                      <li>Set the order (1-6) for each enabled component</li>
                      <li>Adjust regex patterns if needed for specific formats</li>
                      <li>Use Quick Presets to load common configurations</li>
                      <li>The backend will use this configuration to extract line numbers from P&ID PDFs</li>
                    </ul>
                  </div>
                </div>
              </div>
            </div>

            {/* Modal Footer */}
            <div className="px-6 py-4 bg-gray-50 border-t border-gray-200 flex justify-end space-x-3">
              <button
                onClick={() => setShowFormatConfigModal(false)}
                className="px-6 py-3 border-2 border-gray-300 text-gray-700 rounded-xl hover:bg-gray-100 transition-colors font-semibold"
              >
                Cancel
              </button>
              <button
                onClick={() => {
                  // Validate at least one component is enabled
                  const hasEnabled = lineNumberFormat.components.some(c => c.enabled);
                  if (!hasEnabled) {
                    alert('Please enable at least one component');
                    return;
                  }
                  setShowFormatConfigModal(false);
                  // Save to localStorage for persistence
                  localStorage.setItem('designiq_line_format_config', JSON.stringify(lineNumberFormat));
                }}
                className="px-6 py-3 bg-gradient-to-r from-purple-600 to-indigo-600 text-white rounded-xl hover:shadow-lg transition-all font-semibold"
              >
                Save Configuration
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default DesignIQLists;

