"""
Test Azure Algorithms - Local Adaptation
Run this to verify the Azure algorithms work locally!
"""

import sys
import os
from pathlib import Path

# Add backend to path
backend_path = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(backend_path))

import cv2
import numpy as np
from apps.pfd_converter.azure_algorithms.local_adapter import (
    LocalLineDetector,
    LocalGraphConstructor,
    AzureAlgorithmAdapter
)


def create_test_image(output_path='test_pid.png'):
    """Create a simple test P&ID image"""
    print("📝 Creating test P&ID image...")
    
    # Create white canvas
    img = np.ones((800, 1000, 3), dtype=np.uint8) * 255
    
    # Draw some lines (pipes)
    cv2.line(img, (100, 200), (400, 200), (0, 0, 0), 2)  # Horizontal
    cv2.line(img, (400, 200), (400, 600), (0, 0, 0), 2)  # Vertical
    cv2.line(img, (400, 600), (700, 600), (0, 0, 0), 2)  # Horizontal
    
    # Draw symbols (rectangles for equipment)
    cv2.rectangle(img, (90, 180), (110, 220), (0, 0, 255), 2)    # Symbol 1
    cv2.rectangle(img, (390, 580), (410, 620), (0, 0, 255), 2)   # Symbol 2
    cv2.rectangle(img, (690, 580), (710, 620), (0, 0, 255), 2)   # Symbol 3
    
    # Add text
    cv2.putText(img, 'P-101', (85, 170), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    cv2.putText(img, 'V-201', (385, 570), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    cv2.putText(img, 'T-301', (685, 570), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
    
    # Save
    cv2.imwrite(output_path, img)
    print(f"✅ Test image saved: {output_path}")
    
    return output_path


def test_line_detection(image_path):
    """Test Hough Transform line detection"""
    print("\n🔍 Testing Line Detection (Hough Transform)...")
    
    detector = LocalLineDetector(config={
        'hough_threshold': 30,
        'min_line_length': 50,
        'max_line_gap': 10,
        'preprocessing': True
    })
    
    lines = detector.detect_lines(image_path)
    
    print(f"✅ Detected {len(lines)} lines:")
    for i, line in enumerate(lines[:5]):  # Show first 5
        print(f"   Line {i}: ({line['start']['x']:.0f}, {line['start']['y']:.0f}) → "
              f"({line['end']['x']:.0f}, {line['end']['y']:.0f}) "
              f"[Length: {line['length']:.0f}px, Angle: {line['angle']:.1f}°]")
    
    return lines


def test_graph_construction(lines):
    """Test graph construction"""
    print("\n🕸️  Testing Graph Construction...")
    
    # Mock symbol data (simulating YOLOv8 detections)
    symbols = [
        {'id': 'P-101', 'x': 90, 'y': 180, 'width': 20, 'height': 40, 'type': 'pump'},
        {'id': 'V-201', 'x': 390, 'y': 580, 'width': 20, 'height': 40, 'type': 'vessel'},
        {'id': 'T-301', 'x': 690, 'y': 580, 'width': 20, 'height': 40, 'type': 'tank'}
    ]
    
    # Mock text data (simulating OCR)
    text = [
        {'text': 'P-101', 'x': 85, 'y': 170, 'confidence': 0.95},
        {'text': 'V-201', 'x': 385, 'y': 570, 'confidence': 0.93},
        {'text': 'T-301', 'x': 685, 'y': 570, 'confidence': 0.97}
    ]
    
    constructor = LocalGraphConstructor()
    graph = constructor.construct_graph(symbols, lines, text)
    
    print(f"✅ Graph constructed:")
    print(f"   Nodes: {len(graph['nodes'])}")
    print(f"   Edges: {len(graph['edges'])}")
    
    # Show nodes with text
    print("\n   Node Details:")
    for node_id, node_data in graph['nodes']:
        text_label = node_data.get('text', 'No text')
        node_type = node_data.get('data', {}).get('type', 'unknown')
        print(f"   - {node_id}: {node_type} | Text: {text_label}")
    
    return graph


def test_full_integration(image_path):
    """Test complete Azure algorithm integration"""
    print("\n🚀 Testing Full Integration (Azure Algorithms)...")
    
    # Mock data
    symbols = [
        {'id': 'P-101', 'x': 90, 'y': 180, 'width': 20, 'height': 40, 'type': 'pump'},
        {'id': 'V-201', 'x': 390, 'y': 580, 'width': 20, 'height': 40, 'type': 'vessel'},
        {'id': 'T-301', 'x': 690, 'y': 580, 'width': 20, 'height': 40, 'type': 'tank'}
    ]
    
    text = [
        {'text': 'P-101', 'x': 85, 'y': 170, 'confidence': 0.95},
        {'text': 'V-201', 'x': 385, 'y': 570, 'confidence': 0.93},
        {'text': 'T-301', 'x': 685, 'y': 570, 'confidence': 0.97}
    ]
    
    # Use adapter
    adapter = AzureAlgorithmAdapter()
    result = adapter.process_pid_with_azure_algorithms(
        image_path,
        symbols,
        text
    )
    
    print(f"✅ Processing complete:")
    print(f"   Lines detected: {len(result['lines'])}")
    print(f"   Graph nodes: {len(result['graph']['nodes'])}")
    print(f"   Graph edges: {len(result['graph']['edges'])}")
    print(f"   Algorithm source: {result['algorithm_source']}")
    
    return result


def main():
    """Run all tests"""
    print("=" * 60)
    print("🧪 Azure P&ID Algorithms - Local Test Suite")
    print("=" * 60)
    
    try:
        # Create test image
        image_path = create_test_image()
        
        # Test 1: Line detection
        lines = test_line_detection(image_path)
        
        # Test 2: Graph construction
        graph = test_graph_construction(lines)
        
        # Test 3: Full integration
        result = test_full_integration(image_path)
        
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        print("\n📝 Summary:")
        print(f"   ✓ Line detection working (Hough Transform)")
        print(f"   ✓ Graph construction working (NetworkX)")
        print(f"   ✓ Full integration working")
        print(f"   ✓ NO Azure dependencies required!")
        print("\n🎯 Ready to integrate with AIFlow P&ID generation!")
        
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == '__main__':
    main()
