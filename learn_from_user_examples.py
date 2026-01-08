"""
TEST: Learn from User's PFD-P&ID Examples
==========================================
This script learns transformation patterns from your example drawings.
"""

import sys
import os
import django

# Setup Django
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.pfd_converter.intelligent_pattern_learner import IntelligentPatternLearner
import json


def main():
    print("\n" + "="*70)
    print("🧠 INTELLIGENT PATTERN LEARNING FROM YOUR EXAMPLES")
    print("="*70 + "\n")
    
    # Initialize learner
    learner = IntelligentPatternLearner()
    
    # Path to your example drawings
    examples_folder = r"c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\PFD to P&ID\1601"
    
    # Your PFD and P&ID files
    pfd_file = os.path.join(examples_folder, "P16093_PFD.pdf")
    pid_file = os.path.join(examples_folder, "P16093-14-01-08-1602_P&ID.pdf")
    
    print(f"📂 PFD File: {os.path.basename(pfd_file)}")
    print(f"📂 P&ID File: {os.path.basename(pid_file)}")
    
    # Check files exist
    if not os.path.exists(pfd_file):
        print(f"\n❌ PFD file not found: {pfd_file}")
        return
    
    if not os.path.exists(pid_file):
        print(f"\n❌ P&ID file not found: {pid_file}")
        return
    
    print("\n" + "="*70)
    print("STEP 1: DEEP ANALYSIS OF YOUR DRAWINGS")
    print("="*70)
    
    # Learn from the example pair
    print("\n🔍 Analyzing PFD and P&ID using GPT-4 Vision...")
    print("   This will extract ALL details: equipment, valves, instruments, etc.")
    print("   Please wait... (this may take 30-60 seconds)\n")
    
    try:
        pattern = learner.learn_from_example(
            pfd_file,
            pid_file,
            metadata={
                "project": "P16093",
                "area": "1601",
                "client": "ADNOC"
            }
        )
        
        print("\n" + "="*70)
        print("STEP 2: TRANSFORMATION PATTERN EXTRACTED")
        print("="*70)
        
        print(f"\n✅ Pattern ID: {pattern.pattern_id}")
        print(f"✅ Equipment Type: {pattern.equipment_type}")
        print(f"✅ Confidence: {pattern.confidence * 100:.1f}%")
        print(f"\n📋 Transformation Rules Learned: {len(pattern.transformation_rules)}")
        
        print("\n" + "-"*70)
        print("SAMPLE TRANSFORMATION RULES:")
        print("-"*70)
        for i, rule in enumerate(pattern.transformation_rules[:10], 1):
            print(f"{i:2d}. {rule}")
        
        if len(pattern.transformation_rules) > 10:
            print(f"     ... and {len(pattern.transformation_rules) - 10} more rules")
        
        # Display PFD characteristics
        print("\n" + "-"*70)
        print("PFD CHARACTERISTICS IDENTIFIED:")
        print("-"*70)
        pfd_chars = pattern.pfd_characteristics
        if pfd_chars:
            print(json.dumps(pfd_chars, indent=2)[:500] + "...")
        
        # Display P&ID additions
        print("\n" + "-"*70)
        print("P&ID ADDITIONS LEARNED:")
        print("-"*70)
        pid_adds = pattern.pid_additions
        if pid_adds:
            for key, value in pid_adds.items():
                if isinstance(value, list):
                    print(f"  {key}: {len(value)} items")
                else:
                    print(f"  {key}: {value}")
        
        # Save learned patterns
        output_file = "learned_patterns_from_examples.json"
        learner.save_learned_patterns(output_file)
        
        print("\n" + "="*70)
        print("STEP 3: PATTERNS SAVED")
        print("="*70)
        print(f"\n✅ Learned patterns saved to: {output_file}")
        print("   This file contains all transformation rules extracted from your example.")
        
        # Test: Generate P&ID from the same PFD
        print("\n" + "="*70)
        print("STEP 4: TEST GENERATION (Same PFD)")
        print("="*70)
        print("\n🎨 Attempting to regenerate P&ID from the same PFD...")
        print("   This tests if we can replicate your P&ID style.\n")
        
        generated_pid = learner.generate_pid_from_pfd(
            pfd_file,
            project_info={
                "project_name": "P16093",
                "project_code": "P16093",
                "area": "1601"
            },
            reference_pattern_id=pattern.pattern_id
        )
        
        if "error" not in generated_pid:
            print("\n✅ P&ID GENERATED SUCCESSFULLY!")
            print(f"\n📊 Generated P&ID Statistics:")
            
            if "equipment" in generated_pid:
                print(f"   Equipment: {len(generated_pid['equipment'])} items")
            if "valves" in generated_pid:
                print(f"   Valves: {len(generated_pid['valves'])} items")
            if "instruments" in generated_pid:
                print(f"   Instruments: {len(generated_pid['instruments'])} items")
            if "pipes" in generated_pid:
                print(f"   Pipes: {len(generated_pid['pipes'])} items")
            if "control_loops" in generated_pid:
                print(f"   Control Loops: {len(generated_pid['control_loops'])} items")
            
            # Save generated P&ID
            output_pid_file = "generated_pid_test.json"
            with open(output_pid_file, 'w', encoding='utf-8') as f:
                json.dump(generated_pid, f, indent=2, ensure_ascii=False)
            
            print(f"\n✅ Generated P&ID saved to: {output_pid_file}")
            
            # Show sample equipment
            if "equipment" in generated_pid and generated_pid["equipment"]:
                print("\n" + "-"*70)
                print("SAMPLE EQUIPMENT (First 3):")
                print("-"*70)
                for eq in generated_pid["equipment"][:3]:
                    print(f"\n  Tag: {eq.get('tag', 'N/A')}")
                    print(f"  Type: {eq.get('type', 'N/A')}")
                    print(f"  Description: {eq.get('description', 'N/A')}")
                    if 'nozzles' in eq:
                        print(f"  Nozzles: {len(eq['nozzles'])}")
            
            # Show sample valves
            if "valves" in generated_pid and generated_pid["valves"]:
                print("\n" + "-"*70)
                print("SAMPLE VALVES (First 5):")
                print("-"*70)
                for valve in generated_pid["valves"][:5]:
                    print(f"\n  Tag: {valve.get('tag', 'N/A')}")
                    print(f"  Type: {valve.get('type', 'N/A')}")
                    print(f"  Size: {valve.get('size', 'N/A')}")
                    print(f"  Location: {valve.get('location', 'N/A')}")
            
            # Show sample instruments
            if "instruments" in generated_pid and generated_pid["instruments"]:
                print("\n" + "-"*70)
                print("SAMPLE INSTRUMENTS (First 5):")
                print("-"*70)
                for inst in generated_pid["instruments"][:5]:
                    print(f"\n  Tag: {inst.get('tag', 'N/A')}")
                    print(f"  Type: {inst.get('type', 'N/A')}")
                    print(f"  Description: {inst.get('description', 'N/A')}")
                    print(f"  Range: {inst.get('range', 'N/A')}")
            
        else:
            print(f"\n❌ Generation failed: {generated_pid['error']}")
        
        print("\n" + "="*70)
        print("✅ LEARNING AND TESTING COMPLETE!")
        print("="*70)
        
        print("\n📚 Summary:")
        print(f"   • Learned from your example PFD-P&ID pair")
        print(f"   • Extracted {len(pattern.transformation_rules)} transformation rules")
        print(f"   • Saved patterns to: {output_file}")
        print(f"   • Generated test P&ID using learned patterns")
        print(f"   • Test P&ID saved to: {output_pid_file}")
        
        print("\n🎯 Next Steps:")
        print("   1. Review the generated P&ID JSON file")
        print("   2. Compare with your original P&ID")
        print("   3. The system will now use these patterns for new PFDs")
        print("   4. Upload any new PFD to get P&ID in your exact style!")
        
        print("\n" + "="*70 + "\n")
        
    except Exception as e:
        print(f"\n❌ Error during learning: {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
