"""
Real-time monitor for OpenAI Vision FROM-TO detection.
Run this while uploading a P&ID to see the Vision analysis in action.
"""
import subprocess
import time
import sys

print("\n" + "="*80)
print("🔍 REAL-TIME OpenAI Vision FROM-TO Monitor")
print("="*80)
print("\nWaiting for P&ID upload...")
print("Expected logs:")
print("  🧠 SMART OpenAI Vision FROM-TO detection")
print("  🔍 Calling OpenAI Vision API (max_tokens=4000...)")
print("  📥 OpenAI Response: XXXX chars")
print("  ✅ OpenAI Vision FROM-TO RESULTS:")
print("\nPress Ctrl+C to stop monitoring\n")
print("="*80 + "\n")

try:
    # Start monitoring logs
    cmd = [
        "docker", "logs", "-f", "--tail", "50", "aiflow_backend"
    ]
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    
    keywords = [
        "SMART OpenAI",
        "Vision FROM-TO",
        "Calling OpenAI",
        "OpenAI Response",
        "FROM-TO RESULTS",
        "Total lines analyzed",
        "Lines with FROM",
        "Lines with TO",
        "PHASE 3A",
        "PHASE 3B",
        "ERROR",
        "FAILED"
    ]
    
    for line in process.stdout:
        # Check if line contains any keywords
        if any(keyword in line for keyword in keywords):
            # Highlight important lines
            if "✅" in line or "RESULTS" in line:
                print(f"\033[92m{line.strip()}\033[0m")  # Green
            elif "❌" in line or "ERROR" in line or "FAILED" in line:
                print(f"\033[91m{line.strip()}\033[0m")  # Red
            elif "🧠" in line or "OpenAI" in line:
                print(f"\033[94m{line.strip()}\033[0m")  # Blue
            else:
                print(line.strip())
                
except KeyboardInterrupt:
    print("\n\n" + "="*80)
    print("Monitoring stopped")
    print("="*80 + "\n")
    sys.exit(0)
except Exception as e:
    print(f"\n❌ Error: {e}")
    sys.exit(1)
