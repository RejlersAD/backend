"""
Learn PFD to P&ID Conversion Patterns
Uses the pattern learning service to analyze PFD-P&ID pairs
"""
import os
import sys
import json
import django

# Setup Django
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from apps.pfd_converter.pattern_learning_service import learn_patterns_from_pair
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format='%(levelname)s:%(name)s:%(message)s')
logger = logging.getLogger(__name__)

def main():
    """Main execution"""
    
    # File paths
    pfd_path = r"c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\PFD to P&ID\1601\P16093_PFD.pdf"
    pid_path = r"c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\PFD to P&ID\1601\P16093-14-01-08-1602_P&ID.pdf"
    pfd_analysis_path = r"c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\backend\P16093_PFD_Analysis.json"
    
    output_dir = Path(r"c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\backend")
    output_path = output_dir / "Learned_PFD_to_PID_Patterns.json"
    
    logger.info("=" * 100)
    logger.info("PFD TO P&ID PATTERN LEARNING SYSTEM (GPT-4 Vision)")
    logger.info("=" * 100)
    
    # Load existing PFD analysis if available
    pfd_data = None
    if os.path.exists(pfd_analysis_path):
        logger.info(f"\n📂 Loading PFD analysis: {pfd_analysis_path}")
        with open(pfd_analysis_path, 'r') as f:
            pfd_data = json.load(f)
        logger.info(f"✅ PFD data loaded")
    
    # Learn patterns
    logger.info(f"\n🧠 Learning conversion patterns from:")
    logger.info(f"   PFD: {os.path.basename(pfd_path)}")
    logger.info(f"   P&ID: {os.path.basename(pid_path)}")
    
    patterns = learn_patterns_from_pair(
        pfd_path=pfd_path,
        pid_path=pid_path,
        pfd_data=pfd_data,
        output_path=output_path
    )
    
    # Display summary
    logger.info("\n" + "=" * 100)
    logger.info("LEARNED PATTERNS SUMMARY")
    logger.info("=" * 100)
    
    if isinstance(patterns, dict) and 'raw_content' not in patterns:
        logger.info("\n✅ Successfully learned patterns for:")
        for key in patterns.keys():
            logger.info(f"   - {key}")
    else:
        logger.warning("\n⚠️ Patterns require manual processing")
    
    logger.info(f"\n📁 Patterns saved to: {output_path}")
    logger.info("\n" + "=" * 100)
    logger.info("✅ PATTERN LEARNING COMPLETE!")
    logger.info("=" * 100)


if __name__ == "__main__":
    main()
