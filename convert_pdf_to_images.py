"""
Convert PDF drawings to images for GPT-4 Vision analysis
"""

import os
import sys
from pdf2image import convert_from_path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def convert_pdf_to_images(pdf_path: str, output_folder: str = None) -> list:
    """
    Convert PDF to high-resolution images
    """
    try:
        logger.info(f"Converting {pdf_path} to images...")
        
        # Create output folder
        if output_folder is None:
            output_folder = os.path.dirname(pdf_path)
        
        # Convert PDF to images (high DPI for details)
        images = convert_from_path(
            pdf_path,
            dpi=300,  # High resolution
            fmt='png'
        )
        
        # Save images
        output_paths = []
        base_name = os.path.splitext(os.path.basename(pdf_path))[0]
        
        for i, image in enumerate(images):
            output_path = os.path.join(output_folder, f"{base_name}_page_{i+1}.png")
            image.save(output_path, 'PNG')
            output_paths.append(output_path)
            logger.info(f"  Saved: {output_path}")
        
        logger.info(f"✅ Converted {len(images)} pages")
        return output_paths
        
    except Exception as e:
        logger.error(f"❌ Conversion failed: {str(e)}")
        logger.info("   Install poppler: https://github.com/oschwartz10612/poppler-windows/releases/")
        return []


if __name__ == "__main__":
    # Convert your example drawings
    examples_folder = r"c:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow\Documents\PFD to P&ID\1601"
    
    pfd_file = os.path.join(examples_folder, "P16093_PFD.pdf")
    pid_file = os.path.join(examples_folder, "P16093-14-01-08-1602_P&ID.pdf")
    
    print("Converting PDFs to images for GPT-4 Vision...\n")
    
    pfd_images = convert_pdf_to_images(pfd_file)
    pid_images = convert_pdf_to_images(pid_file)
    
    print(f"\n✅ Conversion complete!")
    print(f"   PFD images: {len(pfd_images)}")
    print(f"   P&ID images: {len(pid_images)}")
