"""
Setup script to copy reference P&ID drawings for AI learning
This script copies your reference P&ID PDFs to the system's reference folder
"""
import os
import shutil

# Define paths
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
REFERENCE_DIR = os.path.join(CURRENT_DIR, 'media', 'reference_pids')

# Your reference P&ID files (adjust these paths if needed)
REFERENCE_FILES = [
    r"C:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\PFD to P&ID\1601\P16093-16-01-08-1689_P&ID1.pdf",
    r"C:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\PFD to P&ID\1601\P16093-14-01-08-1603_P&ID.pdf"
]

def setup_reference_pids():
    """Copy reference P&ID files to the reference directory"""
    
    # Ensure reference directory exists
    os.makedirs(REFERENCE_DIR, exist_ok=True)
    print(f"✅ Reference directory created/verified: {REFERENCE_DIR}")
    
    # Copy each reference file
    copied_count = 0
    for ref_file in REFERENCE_FILES:
        if os.path.exists(ref_file):
            filename = os.path.basename(ref_file)
            dest_path = os.path.join(REFERENCE_DIR, filename)
            
            shutil.copy2(ref_file, dest_path)
            file_size = os.path.getsize(dest_path) / (1024 * 1024)  # MB
            print(f"✅ Copied: {filename} ({file_size:.2f} MB)")
            copied_count += 1
        else:
            print(f"⚠️ File not found: {ref_file}")
    
    print(f"\n📊 Summary: {copied_count}/{len(REFERENCE_FILES)} reference P&IDs copied")
    
    # List all files in reference directory
    print(f"\n📁 Reference P&IDs available:")
    for filename in os.listdir(REFERENCE_DIR):
        filepath = os.path.join(REFERENCE_DIR, filename)
        file_size = os.path.getsize(filepath) / (1024 * 1024)
        print(f"   - {filename} ({file_size:.2f} MB)")
    
    print("\n✅ Setup complete! The system will now use these reference P&IDs for AI learning.")
    print("   When generating P&IDs, the AI will analyze these references to match their style.")

if __name__ == "__main__":
    setup_reference_pids()
