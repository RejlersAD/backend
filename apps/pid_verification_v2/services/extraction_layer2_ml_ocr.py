"""
PID Verification V2 - Layer 2: ML OCR Fallback Extraction
==========================================================
Implements ML-based OCR fallback engines when Layer 1 confidence is low:
  - EasyOCR (ML-based, good for handwritten/low-quality scans)
  - PaddleOCR (handles rotated/vertical text)

This layer is CONDITIONALLY executed (free, but slower than Layer 1).
Triggered when:
  - OCR confidence < threshold (default 60%)
  - Too few items found (< 5 tags/lines)
  - Yellow regions detected (handwritten notes)
"""

import os
import logging
import time
from typing import Dict, List, Optional
from PIL import Image

# ML OCR Engines
try:
    import easyocr
    EASYOCR_AVAILABLE = True
except ImportError:
    EASYOCR_AVAILABLE = False
    logging.warning("[Layer2] easyocr not installed - install with: pip install easyocr")

try:
    from paddleocr import PaddleOCR
    PADDLEOCR_AVAILABLE = True
except ImportError:
    PADDLEOCR_AVAILABLE = False
    logging.warning("[Layer2] paddleocr not installed - install with: pip install paddleocr")

# Import configuration
from ..extraction_config import LAYER1_OCR_CONFIG

logger = logging.getLogger(__name__)


class Layer2MLOCRExtractor:
    """
    Layer 2: ML-based OCR fallback service.
    
    Provides enhanced OCR using ML models when Layer 1 results are weak.
    """
    
    def __init__(self):
        """Initialize Layer 2 ML OCR extractors."""
        self.easyocr_reader = None
        self.paddleocr_reader = None
        self.config = LAYER1_OCR_CONFIG.get('fallback_engines', [])
    
    def should_trigger(self, layer1_result: Dict) -> bool:
        """
        Determine if Layer 2 fallback should be triggered.
        
        Args:
            layer1_result: Results from Layer 1 extraction
        
        Returns:
            True if Layer 2 should run, False otherwise
        """
        # Get trigger conditions from config
        easyocr_config = next((e for e in self.config if e['name'] == 'easyocr'), {})
        trigger_config = easyocr_config.get('trigger_condition', {})
        
        min_confidence = trigger_config.get('min_confidence', 60)
        min_tags_found = trigger_config.get('min_tags_found', 5)
        
        # Calculate Layer 1 metrics
        avg_confidence = 0
        total_confidence = 0
        page_count = 0
        
        for page_result in layer1_result.get('per_page_results', []):
            conf = page_result.get('confidence_score', 0)
            if conf > 0:
                total_confidence += conf
                page_count += 1
        
        avg_confidence = total_confidence / page_count if page_count > 0 else 0
        
        # Count total tags/items found
        aggregated = layer1_result.get('aggregated_data', {})
        total_items = (
            len(aggregated.get('equipment_tags', [])) +
            len(aggregated.get('line_numbers', [])) +
            len(aggregated.get('instrument_tags', []))
        )
        
        # Trigger if confidence low OR few items found
        should_run = avg_confidence < min_confidence or total_items < min_tags_found
        
        if should_run:
            logger.info(
                f"[Layer2] Triggering ML OCR fallback: "
                f"confidence={avg_confidence:.1f}% (threshold={min_confidence}%), "
                f"items_found={total_items} (threshold={min_tags_found})"
            )
        else:
            logger.info(
                f"[Layer2] Skipping ML OCR: "
                f"confidence={avg_confidence:.1f}% >= {min_confidence}%, "
                f"items_found={total_items} >= {min_tags_found}"
            )
        
        return should_run
    
    def extract_from_image(self, image: Image.Image, page_num: int) -> Dict:
        """
        Extract text from image using ML OCR engines.
        
        Args:
            image: PIL Image object
            page_num: Page number being processed
        
        Returns:
            {
                'easyocr_result': {...},
                'paddleocr_result': {...},
                'merged_result': {...},
                'processing_time': float,
            }
        """
        start_time = time.time()
        logger.info(f"[Layer2] Running ML OCR on page {page_num}")
        
        result = {
            'page_num': page_num,
            'easyocr_result': {},
            'paddleocr_result': {},
            'merged_result': {},
            'processing_time': 0.0,
        }
        
        # Run EasyOCR
        if self._is_engine_enabled('easyocr') and EASYOCR_AVAILABLE:
            logger.info(f"[Layer2] Running EasyOCR on page {page_num}")
            result['easyocr_result'] = self._run_easyocr(image)
        
        # Run PaddleOCR
        if self._is_engine_enabled('paddleocr') and PADDLEOCR_AVAILABLE:
            logger.info(f"[Layer2] Running PaddleOCR on page {page_num}")
            result['paddleocr_result'] = self._run_paddleocr(image)
        
        # Merge results
        result['merged_result'] = self._merge_ml_results(result)
        
        end_time = time.time()
        result['processing_time'] = round(end_time - start_time, 2)
        
        logger.info(f"[Layer2] ML OCR complete for page {page_num} in {result['processing_time']}s")
        
        return result
    
    def _run_easyocr(self, image: Image.Image) -> Dict:
        """
        Run EasyOCR on image.
        
        Returns:
            {
                'text': str,
                'detections': [{text, bbox, confidence}],
                'avg_confidence': float,
            }
        """
        try:
            # Initialize EasyOCR reader (lazy loading)
            if self.easyocr_reader is None:
                easyocr_config = next((e for e in self.config if e['name'] == 'easyocr'), {})
                languages = easyocr_config.get('languages', ['en'])
                gpu = easyocr_config.get('gpu', False)
                
                logger.info(f"[EasyOCR] Initializing reader: languages={languages}, gpu={gpu}")
                self.easyocr_reader = easyocr.Reader(languages, gpu=gpu)
            
            # Convert PIL Image to numpy array
            import numpy as np
            image_np = np.array(image)
            
            # Run detection
            detections = self.easyocr_reader.readtext(image_np)
            
            # Process results
            text_parts = []
            detection_list = []
            confidences = []
            
            for detection in detections:
                bbox = detection[0]  # [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
                text = detection[1]
                confidence = detection[2]
                
                text_parts.append(text)
                confidences.append(confidence * 100)  # Convert to percentage
                
                # Convert bbox to [x, y, width, height]
                x_coords = [point[0] for point in bbox]
                y_coords = [point[1] for point in bbox]
                bbox_normalized = [
                    min(x_coords),
                    min(y_coords),
                    max(x_coords) - min(x_coords),
                    max(y_coords) - min(y_coords),
                ]
                
                detection_list.append({
                    'text': text,
                    'bbox': bbox_normalized,
                    'confidence': confidence * 100,
                })
            
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0
            
            result = {
                'text': ' '.join(text_parts),
                'detections': detection_list,
                'avg_confidence': avg_confidence,
                'detection_count': len(detections),
            }
            
            logger.debug(f"[EasyOCR] Found {len(detections)} text regions, avg confidence: {avg_confidence:.1f}%")
            return result
        
        except Exception as e:
            logger.error(f"[EasyOCR] Extraction failed: {str(e)}")
            return {
                'error': str(e),
                'text': '',
                'detections': [],
                'avg_confidence': 0,
            }
    
    def _run_paddleocr(self, image: Image.Image) -> Dict:
        """
        Run PaddleOCR on image.
        
        Returns:
            {
                'text': str,
                'detections': [{text, bbox, confidence}],
                'avg_confidence': float,
            }
        """
        try:
            # Initialize PaddleOCR (lazy loading)
            if self.paddleocr_reader is None:
                paddleocr_config = next((e for e in self.config if e['name'] == 'paddleocr'), {})
                lang = paddleocr_config.get('lang', 'en')
                use_gpu = paddleocr_config.get('use_gpu', False)
                use_angle_cls = paddleocr_config.get('use_angle_cls', True)
                
                logger.info(f"[PaddleOCR] Initializing: lang={lang}, gpu={use_gpu}, angle_cls={use_angle_cls}")
                self.paddleocr_reader = PaddleOCR(
                    lang=lang,
                    use_gpu=use_gpu,
                    use_angle_cls=use_angle_cls,
                    show_log=False,
                )
            
            # Convert PIL Image to numpy array
            import numpy as np
            image_np = np.array(image)
            
            # Run detection
            result_raw = self.paddleocr_reader.ocr(image_np, cls=True)
            
            # Process results
            text_parts = []
            detection_list = []
            confidences = []
            
            if result_raw and result_raw[0]:  # PaddleOCR returns nested list
                for line in result_raw[0]:
                    bbox = line[0]  # [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
                    text_info = line[1]  # (text, confidence)
                    text = text_info[0]
                    confidence = text_info[1]
                    
                    text_parts.append(text)
                    confidences.append(confidence * 100)
                    
                    # Convert bbox to [x, y, width, height]
                    x_coords = [point[0] for point in bbox]
                    y_coords = [point[1] for point in bbox]
                    bbox_normalized = [
                        min(x_coords),
                        min(y_coords),
                        max(x_coords) - min(x_coords),
                        max(y_coords) - min(y_coords),
                    ]
                    
                    detection_list.append({
                        'text': text,
                        'bbox': bbox_normalized,
                        'confidence': confidence * 100,
                    })
            
            avg_confidence = sum(confidences) / len(confidences) if confidences else 0
            
            result = {
                'text': ' '.join(text_parts),
                'detections': detection_list,
                'avg_confidence': avg_confidence,
                'detection_count': len(detection_list),
            }
            
            logger.debug(f"[PaddleOCR] Found {len(detection_list)} text regions, avg confidence: {avg_confidence:.1f}%")
            return result
        
        except Exception as e:
            logger.error(f"[PaddleOCR] Extraction failed: {str(e)}")
            return {
                'error': str(e),
                'text': '',
                'detections': [],
                'avg_confidence': 0,
            }
    
    def _merge_ml_results(self, layer2_result: Dict) -> Dict:
        """
        Merge EasyOCR and PaddleOCR results.
        
        Returns:
            {
                'text': str,
                'all_detections': [{text, bbox, confidence, source}],
                'best_confidence': float,
            }
        """
        merged = {
            'text': '',
            'all_detections': [],
            'best_confidence': 0,
        }
        
        # Merge EasyOCR
        if 'easyocr_result' in layer2_result and 'text' in layer2_result['easyocr_result']:
            easy_result = layer2_result['easyocr_result']
            merged['text'] += easy_result.get('text', '') + ' '
            for det in easy_result.get('detections', []):
                det['source'] = 'easyocr'
                merged['all_detections'].append(det)
            merged['best_confidence'] = max(merged['best_confidence'], easy_result.get('avg_confidence', 0))
        
        # Merge PaddleOCR
        if 'paddleocr_result' in layer2_result and 'text' in layer2_result['paddleocr_result']:
            paddle_result = layer2_result['paddleocr_result']
            merged['text'] += paddle_result.get('text', '')
            for det in paddle_result.get('detections', []):
                det['source'] = 'paddleocr'
                merged['all_detections'].append(det)
            merged['best_confidence'] = max(merged['best_confidence'], paddle_result.get('avg_confidence', 0))
        
        merged['text'] = merged['text'].strip()
        
        return merged
    
    def _is_engine_enabled(self, engine_name: str) -> bool:
        """Check if an engine is enabled in config."""
        for engine in self.config:
            if engine['name'] == engine_name:
                return engine.get('enabled', False)
        return False
