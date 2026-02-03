import React, { useState, useEffect, useRef } from 'react';
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

  const currentListConfig = LIST_TYPES[selectedListType];

  // Page controls (Fullscreen, Sidebar, Auto-refresh)
  const pageControls = usePageControls({
    refreshCallback: () => fetchData(true),
    autoRefreshInterval: 30000, // 30 seconds
    storageKey: `designiq_lists_${selectedListType}`,
  });

  useEffect(() => {
    fetchData();
  }, [selectedListType, statusFilter]);

  const fetchData = async (isAutoRefresh = false) => {
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
  };

  const handleSearch = () => {
    fetchData();
  };

  const handleExport = async () => {
    try {
      const token = localStorage.getItem(STORAGE_KEYS.ACCESS_TOKEN);
      const response = await fetch(
        `${API_BASE_URL}/designiq/lists/export/?list_type=${selectedListType}`,
        {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json'
          }
        }
      );
      
      if (response.ok) {
        const data = await response.json();
        // Download as JSON
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `${selectedListType}_export_${new Date().toISOString().split('T')[0]}.json`;
        a.click();
      }
    } catch (error) {
      console.error('Error exporting data:', error);
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
      setExtractedData({
        lines: data.extracted_lines || [],
        fileName: file.name,
        itemsCreated: data.items_created || 0
      });
      setShowPreviewModal(true);
      setUploadResult({
        success: true,
        message: `Successfully extracted ${data.extracted_lines?.length || 0} line numbers from ${file.name}`,
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
        <div className="flex items-center justify-between">
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

          {/* Action Buttons */}
          <div className="flex items-center space-x-3">
            <button
              onClick={handleExport}
              className="flex items-center px-4 py-2 border border-gray-300 rounded-lg hover:bg-gray-50"
            >
              <ArrowDownTrayIcon className="w-5 h-5 mr-2" />
              Export
            </button>
            
            {/* P&ID Upload Button - Only for Line List */}
            {selectedListType === 'line_list' && (
              <>
                <input
                  type="file"
                  ref={fileInputRef}
                  accept=".pdf"
                  onChange={(e) => handlePIDUpload(e, false)}
                  className="hidden"
                />
                <input
                  type="file"
                  ref={fileInputWithAreaRef}
                  accept=".pdf"
                  onChange={(e) => handlePIDUpload(e, true)}
                  className="hidden"
                />
                <input
                  type="file"
                  ref={fileInputOffshoreRef}
                  accept=".pdf"
                  onChange={(e) => handlePIDUpload(e, false, 'offshore')}
                  className="hidden"
                />
                <button
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploadingPID || processing}
                  className={`flex items-center px-4 py-2 border rounded-lg ${
                    uploadingPID || processing
                      ? 'border-gray-200 bg-gray-100 text-gray-400 cursor-not-allowed'
                      : 'border-blue-500 text-blue-600 hover:bg-blue-50'
                  }`}
                >
                  {processing ? (
                    <>
                      <div className="animate-spin rounded-full h-5 w-5 border-2 border-blue-600 border-t-transparent mr-2"></div>
                      Processing OCR...
                    </>
                  ) : (
                    <>
                      <ArrowUpTrayIcon className="w-5 h-5 mr-2" />
                      {uploadingPID ? 'Uploading...' : ' ADNOC Onshore'}
                    </>
                  )}
                </button>
                <button
                  onClick={() => fileInputWithAreaRef.current?.click()}
                  disabled={uploadingPID || processing}
                  className={`flex items-center px-4 py-2 border rounded-lg ml-2 ${
                    uploadingPID || processing
                      ? 'border-gray-200 bg-gray-100 text-gray-400 cursor-not-allowed'
                      : 'border-green-500 text-green-600 hover:bg-green-50'
                  }`}
                >
                  {processing ? (
                    <>
                      <div className="animate-spin rounded-full h-5 w-5 border-2 border-green-600 border-t-transparent mr-2"></div>
                      Processing OCR...
                    </>
                  ) : (
                    <>
                      <ArrowUpTrayIcon className="w-5 h-5 mr-2" />
                      {uploadingPID ? 'Uploading...' : ' General'}
                    </>
                  )}
                </button>
              </>
            )}

            <button
              onClick={() => fileInputOffshoreRef.current?.click()}
              disabled={uploadingPID || processing}
              className={`flex items-center px-4 py-2 border rounded-lg ml-2 ${
                uploadingPID || processing
                  ? 'border-gray-200 bg-gray-100 text-gray-400 cursor-not-allowed'
                  : 'border-purple-500 text-purple-600 hover:bg-purple-50'
              }`}
            >
              {processing ? (
                <>
                  <div className="animate-spin rounded-full h-5 w-5 border-2 border-purple-600 border-t-transparent mr-2"></div>
                  Processing...
                </>
              ) : (
                <>
                  <ArrowUpTrayIcon className="w-5 h-5 mr-2" />
                  {uploadingPID ? 'Uploading...' : 'ADNOC Offshore'}
                </>
              )}
            </button>
          </div>
        </div>

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
      </div>

      {/* Items Table */}
      <div className="bg-white rounded-lg shadow-sm overflow-hidden">
        {loading ? (
          <div className="p-12 text-center">
            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-indigo-600 mx-auto"></div>
            <p className="mt-4 text-gray-600">Loading items...</p>
          </div>
        ) : items.length === 0 ? (
          <div className="p-12 text-center">
            <currentListConfig.icon className="mx-auto h-12 w-12 text-gray-400" />
            <h3 className="mt-2 text-sm font-medium text-gray-900">No items found</h3>
            <p className="mt-1 text-sm text-gray-500">Get started by adding a new item.</p>
            <div className="mt-6">
              <button
                className="flex items-center px-4 py-2 border border-purple-500 text-purple-600 rounded-lg hover:bg-purple-50"
            >
              <ArrowUpTrayIcon className="w-5 h-5 mr-2" />
              ADNOC Offshore
              </button>
            </div>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Tag
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Description
                  </th>
                  {selectedListType === 'line_list' && (
                    <>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        FROM
                      </th>
                      <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                        TO
                      </th>
                    </>
                  )}
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Status
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Validated
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Version
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Created By
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {items.map((item) => (
                  <tr key={item.id} className="hover:bg-gray-50">
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                      {item.item_tag}
                    </td>
                    <td className="px-6 py-4 text-sm text-gray-500">
                      {item.description || '-'}
                    </td>
                    {selectedListType === 'line_list' && (
                      <>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">
                          {item.data?.from_line || '-'}
                        </td>
                        <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-700">
                          {item.data?.to_line || '-'}
                        </td>
                      </>
                    )}
                    <td className="px-6 py-4 whitespace-nowrap">
                      {getStatusBadge(item.status)}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      {item.is_validated ? (
                        <CheckCircleIcon className="h-5 w-5 text-green-500" />
                      ) : (
                        <ClockIcon className="h-5 w-5 text-gray-400" />
                      )}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      v{item.version}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      {item.created_by_name || '-'}
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm font-medium">
                      <button
                        onClick={() => navigate(`/designiq/lists/${item.id}`)}
                        className="text-indigo-600 hover:text-indigo-900 mr-4"
                      >
                        View
                      </button>
                      <button className="text-red-600 hover:text-red-900">
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

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
                </div>
                <button
                  onClick={() => {
                    const data = extractedData.lines;
                    
                    // Create workbook
                    const wb = XLSX.utils.book_new();
                    
                    // Prepare data with headers
                    const wsData = [
                      ['Original Detection', 'Fluid Code', 'Size', 'Sequence No', 'PIPR Class', 'Insulation', 'From', 'To']
                    ];
                    
                    // Add data rows
                    data.forEach(row => {
                      wsData.push([
                        row.original_detection || row.line_number || '',
                        row.fluid_code || '',
                        row.size || '',
                        row.sequence_no || '',
                        row.pipr_class || '',
                        row.insulation || '',
                        row.from_line || row.from || '',
                        row.to_line || row.to || ''
                      ]);
                    });
                    
                    // Create worksheet
                    const ws = XLSX.utils.aoa_to_sheet(wsData);
                    
                    // Format specific columns as text to preserve leading zeros
                    const range = XLSX.utils.decode_range(ws['!ref']);
                    for (let R = range.s.r + 1; R <= range.e.r; ++R) {
                      // Column D (index 3) - Sequence No
                      const seqCell = XLSX.utils.encode_cell({ r: R, c: 3 });
                      if (ws[seqCell]) {
                        ws[seqCell].t = 's'; // Set cell type to string
                        ws[seqCell].v = String(ws[seqCell].v); // Ensure value is string
                      }
                      
                      // Column E (index 4) - PIPR Class
                      const piprCell = XLSX.utils.encode_cell({ r: R, c: 4 });
                      if (ws[piprCell]) {
                        ws[piprCell].t = 's'; // Set cell type to string
                        ws[piprCell].v = String(ws[piprCell].v); // Ensure value is string
                      }
                    }
                    
                    // Set column widths
                    ws['!cols'] = [
                      { wch: 20 }, // Original Detection
                      { wch: 12 }, // Fluid Code
                      { wch: 8 },  // Size
                      { wch: 15 }, // Sequence No
                      { wch: 15 }, // PIPR Class
                      { wch: 12 }, // Insulation
                      { wch: 20 }, // From
                      { wch: 20 }  // To
                    ];
                    
                    // Add worksheet to workbook
                    XLSX.utils.book_append_sheet(wb, ws, 'P&ID Lines');
                    
                    // Generate and download
                    XLSX.writeFile(wb, `PID_Lines_${extractedData.fileName.replace('.pdf', '')}_${new Date().toISOString().split('T')[0]}.xlsx`);
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

            {/* Table Preview */}
            <div className="flex-1 overflow-auto px-6 py-4">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gradient-to-r from-indigo-600 to-purple-600 sticky top-0">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider">
                      Original Detection
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider">
                      Fluid Code
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider">
                      Size
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider">
                      Sequence No
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider">
                      PIPR Class
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider">
                      Insulation
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider">
                      From
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-white uppercase tracking-wider">
                      To
                    </th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {extractedData.lines.map((line, index) => (
                    <tr key={index} className="hover:bg-gray-50 transition-colors">
                      <td className="px-4 py-3 whitespace-nowrap text-sm font-bold text-indigo-600">
                        {line.original_detection || line.line_number || '-'}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm font-medium text-gray-900">
                        {line.fluid_code || '-'}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-700">
                        {line.size || '-'}
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
    </div>
  );
};

export default DesignIQLists;




