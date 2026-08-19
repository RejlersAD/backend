#!/usr/bin/env python
"""
YOLOv8 Symbol Detection Service
Integrates trained YOLOv8 model for automatic P&ID symbol detection
"""
import os
import logging
from pathlib import Path
from PIL import Image
from io import BytesIO
import json

logger = logging.getLogger(__name__)

# Check if YOLOv8 is available
try:
    from ultralytics import YOLO
    YOLO_AVAILABLE = True
except ImportError:
    YOLO_AVAILABLE = False
    logger.warning("⚠️ YOLOv8 not available - install ultralytics package")

# Check for PDF conversion
try:
    from pdf2image import convert_from_path, convert_from_bytes
    PDF_CONVERSION_AVAILABLE = True
except ImportError:
    PDF_CONVERSION_AVAILABLE = False
    logger.warning("⚠️ pdf2image not available - PDF conversion disabled")


class YOLOv8SymbolDetector:
    """
    Detects P&ID symbols using trained YOLOv8 model
    """
    
    def __init__(self, model_path=None, confidence_threshold=0.25):
        """
        Initialize YOLOv8 detector
        
        Args:
            model_path: Path to trained model (default: use trained ROBOFLOW model)
            confidence_threshold: Minimum confidence for detections
        """
        self.confidence_threshold = confidence_threshold
        self.model = None
        self.model_loaded = False
        
        if not YOLO_AVAILABLE:
            logger.warning("⚠️ YOLOv8 not available")
            return
        
        # Default to trained ROBOFLOW model
        if model_path is None:
            model_path = '/app/training_data_cache/yolov8/roboflow_trained/weights/best.pt'
        
        # Check if model exists
        if not os.path.exists(model_path):
            logger.warning(f"⚠️ YOLOv8 model not found: {model_path}")
            return
        
        try:
            logger.info(f"📦 Loading YOLOv8 model: {model_path}")
            self.model = YOLO(model_path)
            self.model_loaded = True
            logger.info(f"✅ YOLOv8 model loaded with {len(self.model.names)} classes")
        except Exception as e:
            logger.error(f"❌ Failed to load YOLOv8 model: {e}")
    
    def is_available(self):
        """Check if YOLOv8 detection is available"""
        return YOLO_AVAILABLE and self.model_loaded
    
    def detect_symbols_from_file(self, file_path, dpi=150):
        """
        Detect P&ID symbols in a file (PDF or image)
        
        Args:
            file_path: Path to PDF or image file
            dpi: DPI for PDF conversion (default: 150)
            
        Returns:
            dict: Detection results with symbols, counts, and annotated image
        """
        if not self.is_available():
            return {
                'success': False,
                'error': 'YOLOv8 not available',
                'detections': []
            }
        
        try:
            # Convert PDF to image if needed
            if file_path.lower().endswith('.pdf'):
                if not PDF_CONVERSION_AVAILABLE:
                    return {
                        'success': False,
                        'error': 'PDF conversion not available',
                        'detections': []
                    }
                
                logger.info(f"🔄 Converting PDF to image: {file_path}")
                images = convert_from_path(file_path, dpi=dpi, first_page=1, last_page=1)
                image = images[0]
                
                # Save temporary image
                temp_path = '/tmp/yolov8_detection_temp.jpg'
                image.save(temp_path, 'JPEG')
                detection_path = temp_path
            else:
                detection_path = file_path
            
            # Run YOLOv8 detection
            logger.info(f"🔍 Running YOLOv8 detection (conf={self.confidence_threshold})...")
            results = self.model(detection_path, conf=self.confidence_threshold, verbose=False)
            
            # Process results
            detections = self._process_results(results[0])
            
            # Clean up temp file
            if file_path.lower().endswith('.pdf') and os.path.exists(temp_path):
                os.remove(temp_path)
            
            return {
                'success': True,
                'detections': detections['symbols'],
                'total_symbols': detections['total'],
                'symbol_counts': detections['counts'],
                'confidence_avg': detections['avg_confidence'],
                'model_info': {
                    'classes': len(self.model.names),
                    'confidence_threshold': self.confidence_threshold
                }
            }
            
        except Exception as e:
            logger.error(f"❌ YOLOv8 detection failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'detections': []
            }
    
    def detect_symbols_from_bytes(self, file_bytes, file_type='image', dpi=150):
        """
        Detect P&ID symbols from file bytes
        
        Args:
            file_bytes: File content as bytes
            file_type: 'image' or 'pdf'
            dpi: DPI for PDF conversion
            
        Returns:
            dict: Detection results
        """
        if not self.is_available():
            return {
                'success': False,
                'error': 'YOLOv8 not available',
                'detections': []
            }
        
        try:
            # Convert to image if PDF
            if file_type == 'pdf':
                if not PDF_CONVERSION_AVAILABLE:
                    return {
                        'success': False,
                        'error': 'PDF conversion not available',
                        'detections': []
                    }
                
                images = convert_from_bytes(file_bytes, dpi=dpi, first_page=1, last_page=1)
                image = images[0]
            else:
                image = Image.open(BytesIO(file_bytes))
            
            # Save to temp file for YOLOv8
            temp_path = '/tmp/yolov8_detection_temp.jpg'
            image.save(temp_path, 'JPEG')
            
            # Run detection
            results = self.model(temp_path, conf=self.confidence_threshold, verbose=False)
            
            # Process results
            detections = self._process_results(results[0])
            
            # Clean up
            os.remove(temp_path)
            
            return {
                'success': True,
                'detections': detections['symbols'],
                'total_symbols': detections['total'],
                'symbol_counts': detections['counts'],
                'confidence_avg': detections['avg_confidence']
            }
            
        except Exception as e:
            logger.error(f"❌ YOLOv8 detection failed: {e}")
            return {
                'success': False,
                'error': str(e),
                'detections': []
            }
    
    def _process_results(self, result):
        """Process YOLOv8 detection results"""
        from collections import Counter
        
        boxes = result.boxes
        detections = []
        
        if len(boxes) == 0:
            return {
                'symbols': [],
                'total': 0,
                'counts': {},
                'avg_confidence': 0
            }
        
        classes = boxes.cls.cpu().numpy()
        confidences = boxes.conf.cpu().numpy()
        xyxy = boxes.xyxy.cpu().numpy()  # Bounding box coordinates
        
        total_confidence = 0
        class_names = []
        
        for i, (cls, conf, bbox) in enumerate(zip(classes, confidences, xyxy)):
            class_name = self.model.names[int(cls)]
            class_names.append(class_name)
            total_confidence += conf
            
            detections.append({
                'symbol': class_name,
                'confidence': float(conf),
                'bounding_box': {
                    'x1': float(bbox[0]),
                    'y1': float(bbox[1]),
                    'x2': float(bbox[2]),
                    'y2': float(bbox[3])
                }
            })
        
        # Count symbols
        symbol_counts = dict(Counter(class_names))
        avg_confidence = total_confidence / len(boxes) if len(boxes) > 0 else 0
        
        return {
            'symbols': detections,
            'total': len(detections),
            'counts': symbol_counts,
            'avg_confidence': float(avg_confidence)
        }


# Global detector instance
_detector_instance = None

def get_yolov8_detector():
    """Get global YOLOv8 detector instance (singleton)"""
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = YOLOv8SymbolDetector()
    return _detector_instance


def detect_pid_symbols(file_path):
    """
    Convenience function to detect P&ID symbols in a file
    
    Args:
        file_path: Path to PDF or image file
        
    Returns:
        dict: Detection results
    """
    detector = get_yolov8_detector()
    return detector.detect_symbols_from_file(file_path)
