const fs = require('fs');

const filePath = 'C:\\Users\\Abdullah.Khan\\airflow_frontend\\src\\pages\\DesignIQ\\DesignIQLists.jsx';
let content = fs.readFileSync(filePath, 'utf8');

console.log('📄 Original file size:', content.length);

// 1. Add second ref after fileInputRef
const oldRef = '  const fileInputRef = useRef(null);';
const newRef = `  const fileInputRef = useRef(null);
  const fileInputWithAreaRef = useRef(null);`;

if (content.includes(oldRef)) {
  content = content.replace(oldRef, newRef);
  console.log('✅ Added fileInputWithAreaRef');
} else {
  console.log('❌ Could not find fileInputRef');
}

// 2. Update handlePIDUpload signature
const oldHandlerSignature = '  const handlePIDUpload = async (event) => {';
const newHandlerSignature = '  const handlePIDUpload = async (event, includeArea = false) => {';

if (content.includes(oldHandlerSignature)) {
  content = content.replace(oldHandlerSignature, newHandlerSignature);
  console.log('✅ Updated handlePIDUpload signature');
} else {
  console.log('❌ Could not find handlePIDUpload signature');
}

// 3. Add include_area to FormData
const oldFormData = `      formData.append('pid_file', file);
      formData.append('list_type', 'line_list');`;

const newFormData = `      formData.append('pid_file', file);
      formData.append('list_type', 'line_list');
      formData.append('include_area', includeArea ? 'true' : 'false');`;

if (content.includes(oldFormData)) {
  content = content.replace(oldFormData, newFormData);
  console.log('✅ Added include_area to FormData');
} else {
  console.log('❌ Could not find FormData section');
}

// 4. Replace single button with two buttons
const oldButtonSection = `                <input
                  type="file"
                  ref={fileInputRef}
                  accept=".pdf"
                  onChange={handlePIDUpload}
                  className="hidden"
                />
                <button
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploadingPID || processing}
                  className={\`flex items-center px-4 py-2 border rounded-lg \${
                    uploadingPID || processing
                      ? 'border-gray-200 bg-gray-100 text-gray-400 cursor-not-allowed'
                      : 'border-blue-500 text-blue-600 hover:bg-blue-50'
                  }\`}
                >
                  {processing ? (
                    <>
                      <div className="animate-spin rounded-full h-5 w-5 border-2 border-blue-600 border-t-transparent mr-2"></div>
                      Processing OCR...
                    </>
                  ) : (
                    <>
                      <ArrowUpTrayIcon className="w-5 h-5 mr-2" />
                      {uploadingPID ? 'Uploading...' : '📤 Upload P&ID PDF'}
                    </>
                  )}
                </button>`;

const newButtonSection = `                <input
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
                
                {/* Upload WITHOUT Area Button */}
                <button
                  onClick={() => fileInputRef.current?.click()}
                  disabled={uploadingPID || processing}
                  className={\`flex items-center px-4 py-2 border rounded-lg \${
                    uploadingPID || processing
                      ? 'border-gray-200 bg-gray-100 text-gray-400 cursor-not-allowed'
                      : 'border-blue-500 text-blue-600 hover:bg-blue-50'
                  }\`}
                  title="Standard format: SIZE-FLUID-SEQUENCE-PIPECLASS"
                >
                  {processing ? (
                    <>
                      <div className="animate-spin rounded-full h-5 w-5 border-2 border-blue-600 border-t-transparent mr-2"></div>
                      Processing OCR...
                    </>
                  ) : (
                    <>
                      <ArrowUpTrayIcon className="w-5 h-5 mr-2" />
                      {uploadingPID ? 'Uploading...' : '📤 Upload P&ID (Standard)'}
                    </>
                  )}
                </button>

                {/* Upload WITH Area Button */}
                <button
                  onClick={() => fileInputWithAreaRef.current?.click()}
                  disabled={uploadingPID || processing}
                  className={\`flex items-center px-4 py-2 border rounded-lg \${
                    uploadingPID || processing
                      ? 'border-gray-200 bg-gray-100 text-gray-400 cursor-not-allowed'
                      : 'border-green-500 text-green-600 hover:bg-green-50'
                  }\`}
                  title="With area format: SIZE&quot;-AREA-FLUID-SEQUENCE-PIPECLASS"
                >
                  {processing ? (
                    <>
                      <div className="animate-spin rounded-full h-5 w-5 border-2 border-green-600 border-t-transparent mr-2"></div>
                      Processing OCR...
                    </>
                  ) : (
                    <>
                      <ArrowUpTrayIcon className="w-5 h-5 mr-2" />
                      {uploadingPID ? 'Uploading...' : '📤 Upload P&ID (With Area)'}
                    </>
                  )}
                </button>`;

if (content.includes(oldButtonSection)) {
  content = content.replace(oldButtonSection, newButtonSection);
  console.log('✅ Replaced single button with two buttons');
} else {
  console.log('❌ Could not find button section');
}

// Write back
fs.writeFileSync(filePath, content, 'utf8');

console.log('\n✅ Frontend update complete!');
console.log('📄 New file size:', content.length);
console.log('\n📋 Summary of changes:');
console.log('   1. Added fileInputWithAreaRef for second file input');
console.log('   2. Updated handlePIDUpload to accept includeArea parameter');
console.log('   3. Added include_area field to FormData');
console.log('   4. Replaced single upload button with two buttons:');
console.log('      • Upload P&ID (Standard) - Blue button (without area)');
console.log('      • Upload P&ID (With Area) - Green button (with area)');
