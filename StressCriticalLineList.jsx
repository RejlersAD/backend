import React, { useState, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowUpTrayIcon, ArrowLeftIcon } from '@heroicons/react/24/outline';

const StressCriticalLineList = () => {
  const navigate = useNavigate();
  const fileInputRef = useRef(null);
  const [uploading, setUploading] = useState(false);
  const [selectedFile, setSelectedFile] = useState(null);

  const handleFileSelect = (e) => {
    const file = e.target.files[0];
    if (file) {
      setSelectedFile(file);
    }
  };

  const handleUpload = async () => {
    if (!selectedFile) {
      alert('Please select a P&ID file first');
      return;
    }

    setUploading(true);
    
    // TODO: Implement upload logic for stress critical format
    console.log('Uploading stress critical P&ID:', selectedFile.name);
    
    // Simulate upload delay
    setTimeout(() => {
      setUploading(false);
      alert('Stress Critical Line List upload functionality coming soon!');
      setSelectedFile(null);
    }, 1500);
  };

  return (
    <div className="h-screen bg-gradient-to-br from-gray-50 to-gray-100 dark:from-gray-900 dark:to-gray-800">
      <div className="h-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="mb-6">
          <button
            onClick={() => navigate('/designiq/lists?type=line_list')}
            className="flex items-center text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200 transition-colors"
          >
            <ArrowLeftIcon className="w-5 h-5 mr-2" />
            Back to Line List
          </button>
        </div>

        <div className="bg-white dark:bg-gray-800 rounded-xl shadow-lg border border-gray-200 dark:border-gray-700">
          <div className="p-8">
            <div className="text-center mb-8">
              <h1 className="text-3xl font-bold text-gray-900 dark:text-white mb-2">
                Stress Critical Line List
              </h1>
              <p className="text-gray-600 dark:text-gray-400">
                Upload your P&ID to extract stress critical line information
              </p>
            </div>

            <div className="max-w-2xl mx-auto">
              <div className="border-2 border-dashed border-gray-300 dark:border-gray-600 rounded-lg p-12 text-center hover:border-orange-400 dark:hover:border-orange-500 transition-colors">
                <ArrowUpTrayIcon className="w-16 h-16 mx-auto text-gray-400 dark:text-gray-500 mb-4" />
                
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".pdf"
                  onChange={handleFileSelect}
                  className="hidden"
                />

                {selectedFile ? (
                  <div className="mb-4">
                    <p className="text-sm text-gray-600 dark:text-gray-400 mb-2">Selected file:</p>
                    <p className="text-lg font-semibold text-gray-900 dark:text-white mb-4">
                      {selectedFile.name}
                    </p>
                    <div className="flex gap-3 justify-center">
                      <button
                        onClick={handleUpload}
                        disabled={uploading}
                        className={`px-6 py-3 rounded-lg font-semibold text-white transition-all ${
                          uploading
                            ? 'bg-gray-400 cursor-not-allowed'
                            : 'bg-orange-600 hover:bg-orange-700 shadow-md hover:shadow-lg'
                        }`}
                      >
                        {uploading ? (
                          <span className="flex items-center">
                            <svg className="animate-spin -ml-1 mr-3 h-5 w-5 text-white" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
                              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
                              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                            </svg>
                            Processing...
                          </span>
                        ) : (
                          'Upload & Process'
                        )}
                      </button>
                      <button
                        onClick={() => {
                          setSelectedFile(null);
                          if (fileInputRef.current) fileInputRef.current.value = '';
                        }}
                        className="px-6 py-3 rounded-lg font-semibold text-gray-700 dark:text-gray-300 border-2 border-gray-300 dark:border-gray-600 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  <>
                    <h3 className="text-xl font-semibold text-gray-900 dark:text-white mb-2">
                      Upload Your P&ID
                    </h3>
                    <p className="text-gray-600 dark:text-gray-400 mb-6">
                      Drag and drop or click to browse
                    </p>
                    <button
                      onClick={() => fileInputRef.current?.click()}
                      className="px-8 py-3 bg-orange-600 hover:bg-orange-700 text-white font-semibold rounded-lg shadow-md hover:shadow-lg transition-all"
                    >
                      Select P&ID File
                    </button>
                    <p className="text-sm text-gray-500 dark:text-gray-400 mt-4">
                      Supported format: PDF
                    </p>
                  </>
                )}
              </div>

              <div className="mt-8 bg-orange-50 dark:bg-orange-900/20 border border-orange-200 dark:border-orange-800 rounded-lg p-6">
                <h4 className="font-semibold text-orange-900 dark:text-orange-200 mb-3">
                  Stress Critical Line Format
                </h4>
                <ul className="space-y-2 text-sm text-orange-800 dark:text-orange-300">
                  <li className="flex items-start">
                    <span className="mr-2">•</span>
                    <span>Extracts line numbers with stress critical identification</span>
                  </li>
                  <li className="flex items-start">
                    <span className="mr-2">•</span>
                    <span>Includes stress analysis data and critical parameters</span>
                  </li>
                  <li className="flex items-start">
                    <span className="mr-2">•</span>
                    <span>Processing typically takes 5-8 minutes depending on complexity</span>
                  </li>
                </ul>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default StressCriticalLineList;
