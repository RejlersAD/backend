import re

file_path = r"C:\Users\Abdullah.Khan\airflow_frontend\src\pages\Engineering\Electrical\ElectricalEquipmentDatasheet.jsx"

with open(file_path, 'r', encoding='utf-8') as f:
    content = f.read()

print("🔧 Simplifying transformer document uploads to 1 PDF only...")

# Step 1: Update EQUIPMENT_DOC_TYPES.transformer to only have one PDF
old_transformer_docs = r'''  transformer: \[
    \{
      id: 'mv_trafo_calculation',
      label: 'MV Trafo Calculation',
      description: 'Medium Voltage Transformer Calculation Document',
      icon: '⚡'
    \},
    \{
      id: 'criteria',
      label: 'Criteria',
      description: 'Transformer Selection Criteria Document',
      icon: '📋'
    \},
    \{
      id: 'formula',
      label: 'Formula',
      description: 'Transformer Design Formula Document',
      icon: '🔢'
    \},
    \{
      id: 'lv_trafo_calculation',
      label: 'LV Trafo Calculation',
      description: 'Low Voltage Transformer Calculation Document',
      icon: '⚡'
    \}
  \]'''

new_transformer_docs = '''  transformer: [
    {
      id: 'transformer_calculation',
      label: 'Transformer Sizing Calculation',
      description: 'Transformer Sizing Calculation (Power and Distribution)',
      icon: '⚡'
    }
  ]'''

content = re.sub(old_transformer_docs, new_transformer_docs, content, flags=re.DOTALL)
print("✅ Updated EQUIPMENT_DOC_TYPES.transformer to single PDF")

# Step 2: Update verification handler to only check for 1 PDF
old_handler_validation = r'''    const requiredDocs = \{
      'mv_trafo_calculation': 'MV Trafo Calculation',
      'criteria': 'Criteria',
      'formula': 'Formula',
      'lv_trafo_calculation': 'LV Trafo Calculation'
    \};
    
    const missingDocs = Object\.entries\(requiredDocs\)\.filter\(\(\[key\]\) => !equipmentDocs\[key\]\);
    
    if \(missingDocs\.length > 0\) \{
      setError\(\`Please upload all required documents: \$\{missingDocs\.map\(\(\[, label\]\) => label\)\.join\(', '\)\}\`\);
      return;
    \}'''

new_handler_validation = '''    if (!equipmentDocs.transformer_calculation) {
      setError('Please upload the Transformer Sizing Calculation PDF');
      return;
    }'''

content = re.sub(old_handler_validation, new_handler_validation, content, flags=re.DOTALL)
print("✅ Updated validation to check for single PDF")

# Step 3: Update FormData to only append 2 files
old_formdata = r'''      const formData = new FormData\(\);
      formData\.append\('transformer_datasheet', transformerDatasheet\);
      formData\.append\('mv_calc_document', equipmentDocs\.mv_trafo_calculation\);
      formData\.append\('criteria_document', equipmentDocs\.criteria\);
      formData\.append\('formula_document', equipmentDocs\.formula\);
      formData\.append\('lv_calc_document', equipmentDocs\.lv_trafo_calculation\);'''

new_formdata = '''      const formData = new FormData();
      formData.append('transformer_datasheet', transformerDatasheet);
      formData.append('transformer_calculation', equipmentDocs.transformer_calculation);'''

content = re.sub(old_formdata, new_formdata, content, flags=re.DOTALL)
print("✅ Updated FormData to send 2 files only")

# Step 4: Update button disabled condition
old_button_disabled = r'disabled=\{verifying \|\| !transformerDatasheet \|\| Object\.keys\(equipmentDocs\)\.length < 4\}'
new_button_disabled = 'disabled={verifying || !transformerDatasheet || !equipmentDocs.transformer_calculation}'

content = re.sub(old_button_disabled, new_button_disabled, content)
print("✅ Updated button disabled condition")

# Write the modified content
with open(file_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("\n🎉 ✅ Transformer uploads simplified successfully!")
print("\n📍 Changes:")
print("  ✅ Reduced to 1 PDF: Transformer Sizing Calculation")
print("  ✅ Excel datasheet upload retained")
print("  ✅ Verification handler updated")
print("  ✅ Button validation updated")
print("\n🔄 Frontend will hot-reload automatically")
