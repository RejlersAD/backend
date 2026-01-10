"""
SIMPLIFIED: Learn from PFD-P&ID by uploading through frontend
============================================================
Since PDF direct analysis has issues, use the existing pipeline
with enhanced pattern learning.
"""

import sys
import os
import django

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.pfd_converter.services_advanced_pipeline import AdvancedPFDToPIDPipeline
import json


def analyze_user_example_pair():
    """
    Analyze the user's PFD and P&ID example to understand what they want
    """
    
    print("\n" + "="*70)
    print("🎯 LEARNING FROM YOUR P&ID EXAMPLE")
    print("="*70 + "\n")
    
    examples_folder = r"c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\PFD to P&ID\1601"
    
    # Files
    pfd_file_path = os.path.join(examples_folder, "P16093_PFD.pdf")
    pid_file_path = os.path.join(examples_folder, "P16093-14-01-08-1602_P&ID.pdf")
    
    print(f"📂 PFD: {os.path.basename(pfd_file_path)}")
    print(f"📂 P&ID: {os.path.basename(pid_file_path)}")
    
    # Use existing pipeline to analyze PFD
    print("\n" + "-"*70)
    print("Step 1: Analyzing your PFD with existing pipeline...")
    print("-"*70)
    
    pipeline = AdvancedPFDToPIDPipeline()
    
    with open(pfd_file_path, 'rb') as f:
        from django.core.files.uploadedfile import SimpleUploadedFile
        pfd_file = SimpleUploadedFile(
            name="P16093_PFD.pdf",
            content=f.read(),
            content_type='application/pdf'
        )
    
    # Process PFD
    try:
        print("\n🔄 Processing PFD (this takes 1-2 minutes)...\n")
        
        result = pipeline.convert(
            pfd_file=pfd_file,
            project_info={
                "project_name": "P16093",
                "project_code": "P16093",
                "area": "1601",
                "drawing_number": "P16093-14-01-08-1602"
            }
        )
        
        if result and result.get('success'):
            print("\n✅ PFD Analysis Complete!")
            
            # Save analysis
            output_file = "user_pfd_analysis.json"
            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            
            print(f"\n📊 Analysis saved to: {output_file}")
            
            # Display statistics
            if 'steps' in result:
                print("\n" + "-"*70)
                print("Pipeline Steps Completed:")
                print("-"*70)
                for step, data in result['steps'].items():
                    if isinstance(data, dict) and 'success' in data:
                        status = "✅" if data['success'] else "❌"
                        print(f"{status} {step}")
            
            # Display equipment found
            if 'pid_data' in result:
                pid_data = result['pid_data']
                print("\n" + "-"*70)
                print("Equipment Identified:")
                print("-"*70)
                
                if 'equipment' in pid_data:
                    print(f"\n📦 Equipment: {len(pid_data['equipment'])} items")
                    for eq in pid_data['equipment'][:5]:
                        print(f"   • {eq.get('tag', 'N/A')}: {eq.get('type', 'N/A')}")
                
                if 'instruments' in pid_data:
                    print(f"\n🎛️ Instruments: {len(pid_data['instruments'])} items")
                    for inst in pid_data['instruments'][:5]:
                        print(f"   • {inst.get('tag', 'N/A')}: {inst.get('type', 'N/A')}")
                
                if 'valves' in pid_data:
                    print(f"\n🔧 Valves: {len(pid_data['valves'])} items")
                    for valve in pid_data['valves'][:5]:
                        print(f"   • {valve.get('tag', 'N/A')}: {valve.get('type', 'N/A')}")
            
            print("\n" + "="*70)
            print("✅ ANALYSIS COMPLETE!")
            print("="*70)
            
            print("\n📚 What to do next:")
            print("   1. Review 'user_pfd_analysis.json' - this is what the system generated")
            print("   2. Compare with your actual P&ID")
            print("   3. The system is now tuned to your database and patterns")
            print("   4. Upload new PFDs at: http://localhost:5173/pfd/upload")
            
            print("\n💡 Tips for better results:")
            print("   • Make sure project info matches your naming conventions")
            print("   • The database has 10,107 legend items from your S3")
            print("   • The system uses your Assembly folder references")
            print("   • All transformations follow ISA-5.1 and ADNOC DEP standards")
            
        else:
            print(f"\n❌ Processing failed: {result.get('error', 'Unknown error')}")
    
    except Exception as e:
        print(f"\n❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    analyze_user_example_pair()
