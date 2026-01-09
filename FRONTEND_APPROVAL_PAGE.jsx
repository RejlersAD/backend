// ============================================================================
// APPROVAL PAGE FOR RAD AI FRONTEND
// ============================================================================
// File Location: airflow_frontend/src/pages/Finance/InvoiceApproval.jsx
// Route: /finance/approve/:token
//
// This page displays invoice details and approval form
// Integrates with backend API endpoints for approval workflow
// ============================================================================

import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import axios from 'axios';

// Get API base URL from environment or default
const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000/api/v1';

const InvoiceApproval = () => {
  const { token } = useParams();
  const navigate = useNavigate();
  
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);
  const [approval, setApproval] = useState(null);
  const [invoice, setInvoice] = useState(null);
  const [comments, setComments] = useState('');
  const [success, setSuccess] = useState(null);

  // Fetch approval details on mount
  useEffect(() => {
    fetchApprovalDetails();
  }, [token]);

  const fetchApprovalDetails = async () => {
    try {
      const response = await axios.get(
        `${API_BASE_URL}/finance/approval/${token}/details/`
      );
      setApproval(response.data.approval);
      setInvoice(response.data.invoice);
      setLoading(false);
    } catch (err) {
      setError(err.response?.data?.error || 'Failed to load approval details');
      setLoading(false);
    }
  };

  const handleApprovalDecision = async (decision) => {
    setSubmitting(true);
    setError(null);

    try {
      const response = await axios.post(
        `${API_BASE_URL}/finance/approval/${token}/submit/`,
        {
          decision: decision,
          comments: comments
        }
      );

      setSuccess({
        message: response.data.message,
        next_step: response.data.next_step,
        decision: decision
      });

      // Redirect after 3 seconds
      setTimeout(() => {
        navigate('/finance/invoices');
      }, 3000);

    } catch (err) {
      if (err.response?.data?.error === 'already_processed') {
        setError(`This approval has already been ${err.response.data.status}.`);
      } else {
        setError(err.response?.data?.error || 'Failed to process approval');
      }
    } finally {
      setSubmitting(false);
    }
  };

  // Loading State
  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-indigo-500 to-purple-600">
        <div className="text-center text-white">
          <div className="animate-spin rounded-full h-16 w-16 border-t-2 border-b-2 border-white mx-auto mb-4"></div>
          <p className="text-xl">Loading approval details...</p>
        </div>
      </div>
    );
  }

  // Error State
  if (error && !approval) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="max-w-md w-full bg-white rounded-lg shadow-lg p-8 text-center">
          <div className="text-6xl mb-4">❌</div>
          <h2 className="text-2xl font-bold text-red-600 mb-4">Error</h2>
          <p className="text-gray-700 mb-6">{error}</p>
          <button
            onClick={() => navigate('/finance/invoices')}
            className="px-6 py-3 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700"
          >
            Go to Invoices
          </button>
        </div>
      </div>
    );
  }

  // Success State
  if (success) {
    const isApproved = success.decision === 'approve';
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className={`max-w-2xl w-full rounded-lg shadow-xl p-10 text-center ${
          isApproved ? 'bg-green-50 border-4 border-green-500' : 'bg-red-50 border-4 border-red-500'
        }`}>
          <div className="text-8xl mb-6">{isApproved ? '✅' : '❌'}</div>
          <h1 className={`text-4xl font-bold mb-4 ${
            isApproved ? 'text-green-700' : 'text-red-700'
          }`}>
            Invoice {isApproved ? 'Approved' : 'Rejected'}
          </h1>
          <div className="bg-white rounded-lg p-6 mb-6">
            <p className="text-xl font-semibold text-gray-800 mb-2">
              {invoice.invoice_number}
            </p>
            <p className="text-gray-600">{success.message}</p>
          </div>
          <div className="bg-yellow-100 border-l-4 border-yellow-500 p-4 mb-6">
            <p className="font-semibold text-yellow-800">📧 Next Step:</p>
            <p className="text-yellow-700">{success.next_step}</p>
          </div>
          <p className="text-gray-600 mb-6">
            Redirecting to invoices page in 3 seconds...
          </p>
          <button
            onClick={() => navigate('/finance/invoices')}
            className="px-8 py-3 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 font-semibold"
          >
            Go to Invoices Now
          </button>
        </div>
      </div>
    );
  }

  // Already Decided State
  if (approval.already_decided) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="max-w-2xl w-full bg-yellow-50 border-4 border-yellow-500 rounded-lg shadow-xl p-10 text-center">
          <div className="text-8xl mb-6">⚠️</div>
          <h1 className="text-4xl font-bold text-yellow-700 mb-4">Already Processed</h1>
          <div className="bg-white rounded-lg p-6 mb-6">
            <p className="text-xl font-semibold text-gray-800 mb-2">
              {invoice.invoice_number}
            </p>
            <p className="text-gray-600">
              This approval has already been <span className="font-bold uppercase">{approval.status}</span>
            </p>
            {approval.decision_date && (
              <p className="text-sm text-gray-500 mt-2">
                Decided on: {new Date(approval.decision_date).toLocaleString()}
              </p>
            )}
          </div>
          <button
            onClick={() => navigate('/finance/invoices')}
            className="px-8 py-3 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 font-semibold"
          >
            Go to Invoices
          </button>
        </div>
      </div>
    );
  }

  // Main Approval Form
  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-500 to-purple-600 py-12 px-4">
      <div className="max-w-4xl mx-auto">
        {/* Header */}
        <div className="bg-white rounded-t-xl shadow-lg p-8 text-center">
          <h1 className="text-3xl font-bold text-gray-800 mb-2">
            📋 Invoice Approval Required
          </h1>
          <p className="text-gray-600">
            {approval.level_name} (Level {approval.approval_level})
          </p>
        </div>

        {/* Invoice Details */}
        <div className="bg-white shadow-lg p-8 border-t-2 border-gray-200">
          <h2 className="text-xl font-bold text-gray-800 mb-6 flex items-center">
            <span className="text-2xl mr-2">📄</span>
            Invoice Details
          </h2>
          
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
            <div className="bg-gray-50 p-4 rounded-lg">
              <p className="text-sm text-gray-600 mb-1">Invoice Number</p>
              <p className="text-lg font-semibold text-gray-900">{invoice.invoice_number}</p>
            </div>
            
            <div className="bg-gray-50 p-4 rounded-lg">
              <p className="text-sm text-gray-600 mb-1">Vendor</p>
              <p className="text-lg font-semibold text-gray-900">{invoice.vendor_name || 'N/A'}</p>
            </div>
            
            <div className="bg-gradient-to-r from-green-50 to-green-100 p-4 rounded-lg border-2 border-green-500">
              <p className="text-sm text-green-700 mb-1">Amount</p>
              <p className="text-2xl font-bold text-green-700">
                {invoice.currency} {parseFloat(invoice.total_amount).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
              </p>
            </div>
            
            <div className="bg-gray-50 p-4 rounded-lg">
              <p className="text-sm text-gray-600 mb-1">Type</p>
              <p className="text-lg font-semibold text-gray-900">{invoice.invoice_type_display}</p>
            </div>
            
            <div className="bg-gray-50 p-4 rounded-lg">
              <p className="text-sm text-gray-600 mb-1">Date</p>
              <p className="text-lg font-semibold text-gray-900">{invoice.invoice_date || 'N/A'}</p>
            </div>
            
            <div className="bg-gray-50 p-4 rounded-lg">
              <p className="text-sm text-gray-600 mb-1">Status</p>
              <p className="text-lg font-semibold text-gray-900 uppercase">{invoice.status}</p>
            </div>
          </div>

          {/* PDF Download */}
          {invoice.file_url && (
            <div className="bg-blue-50 border-l-4 border-blue-500 p-4 rounded">
              <p className="flex items-center text-blue-800">
                <span className="text-xl mr-2">📎</span>
                <a 
                  href={invoice.file_url} 
                  target="_blank" 
                  rel="noopener noreferrer"
                  className="font-semibold hover:underline"
                >
                  Download Invoice PDF
                </a>
              </p>
            </div>
          )}
        </div>

        {/* Approval Form */}
        <div className="bg-white rounded-b-xl shadow-lg p-8">
          <h2 className="text-xl font-bold text-gray-800 mb-6">Your Decision</h2>
          
          {/* Comments */}
          <div className="mb-6">
            <label className="block text-gray-700 font-semibold mb-2">
              Comments (Optional)
            </label>
            <textarea
              value={comments}
              onChange={(e) => setComments(e.target.value)}
              rows="4"
              className="w-full px-4 py-3 border-2 border-gray-300 rounded-lg focus:border-indigo-500 focus:outline-none resize-none"
              placeholder="Add any comments about your decision..."
              disabled={submitting}
            />
          </div>

          {/* Error Display */}
          {error && (
            <div className="bg-red-50 border-l-4 border-red-500 p-4 mb-6 rounded">
              <p className="text-red-800 font-semibold">❌ {error}</p>
            </div>
          )}

          {/* Action Buttons */}
          <div className="flex flex-col sm:flex-row gap-4">
            <button
              onClick={() => handleApprovalDecision('approve')}
              disabled={submitting}
              className={`flex-1 py-4 px-8 rounded-lg font-bold text-lg transition-all ${
                submitting 
                  ? 'bg-gray-400 cursor-not-allowed' 
                  : 'bg-green-600 hover:bg-green-700 hover:shadow-lg transform hover:scale-105'
              } text-white`}
            >
              {submitting ? 'Processing...' : '✓ APPROVE'}
            </button>
            
            <button
              onClick={() => handleApprovalDecision('reject')}
              disabled={submitting}
              className={`flex-1 py-4 px-8 rounded-lg font-bold text-lg transition-all ${
                submitting 
                  ? 'bg-gray-400 cursor-not-allowed' 
                  : 'bg-red-600 hover:bg-red-700 hover:shadow-lg transform hover:scale-105'
              } text-white`}
            >
              {submitting ? 'Processing...' : '✗ REJECT'}
            </button>
          </div>

          <p className="text-center text-sm text-gray-500 mt-6">
            Your decision will be recorded and the next level will be notified automatically.
          </p>
        </div>
      </div>
    </div>
  );
};

export default InvoiceApproval;
