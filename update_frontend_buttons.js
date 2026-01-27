const fs = require('fs');
const path = require('path');

const filePath = 'C:\\Users\\Abdullah.Khan\\airflow_frontend\\src\\pages\\DesignIQ\\DesignIQLists.jsx';

// Read the file
let content = fs.readFileSync(filePath, 'utf8');

console.log('Original file size:', content.length);

// 1. Add fileInputWithAreaRef after fileInputRef declaration
const refPattern = /const fileInputRef = React\.useRef\(null\);/;
if (refPattern.test(content)) {
  content = content.replace(
    refPattern,
    `const fileInputRef = React.useRef(null);
  const fileInputWithAreaRef = React.useRef(null);`
  );
  console.log('✅ Added fileInputWithAreaRef');
} else {
  console.log('❌ Could not find fileInputRef declaration');
}

// 2. Replace upload button section
const buttonPattern = /\{\/\* P&ID Upload Button - Only for Line List \*\/\}[\s\S]*?{uploadingPID \? 'Uploading\.\.\.' : '📤 Upload P&ID PDF'}/;

const newButtons = `{/* P&ID Upload Buttons - Only for Line List */}
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
                      {uploadingPID ? 'Uploading...' : '📤 Upload P&ID (Standard)'}`;

if (buttonPattern.test(content)) {
  content = content.replace(buttonPattern, newButtons);
  console.log('✅ Replaced upload button section (part 1)');
} else {
  console.log('❌ Could not find upload button pattern');
}

// 3. Add the second button after the first
const afterFirstButton = /\{uploadingPID \? 'Uploading\.\.\.' : '📤 Upload P&ID \(Standard\)'\}[\s\S]*?<\/>\s*\)\s*\}\s*<\/button>/;

const secondButtonPart = `                    </>
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

if (afterFirstButton.test(content)) {
  content = content.replace(afterFirstButton, secondButtonPart);
  console.log('✅ Added second upload button (with area)');
} else {
  console.log('❌ Could not find first button end pattern');
}

// Write the modified content back
fs.writeFileSync(filePath, content, 'utf8');

console.log('\n✅ Frontend update complete!');
console.log('New file size:', content.length);
console.log('\nChanges made:');
console.log('1. Added fileInputWithAreaRef');
console.log('2. Replaced single upload button with two buttons:');
console.log('   - Upload P&ID (Standard) - blue');
console.log('   - Upload P&ID (With Area) - green');
