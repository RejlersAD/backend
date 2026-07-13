"""
Run fix command and save output to file to avoid console encoding issues
"""
import subprocess
import sys
from datetime import datetime

output_file = f"fix_output_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"

print(f"Running fix command... Output will be saved to: {output_file}")

try:
    # Run the command and capture output
    result = subprocess.run(
        ['railway', 'run', 'python', 'manage.py', 'fix_production_procurement', '--seed'],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace'
    )
    
    # Save to file
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("STDOUT:\n")
        f.write("=" * 80 + "\n")
        f.write(result.stdout)
        f.write("\n\nSTDERR:\n")
        f.write("=" * 80 + "\n")
        f.write(result.stderr)
        f.write("\n\nRETURN CODE: " + str(result.returncode))
    
    print(f"\nCommand completed. Check {output_file} for details.")
    print(f"Return code: {result.returncode}")
    
    # Try to print summary
    if result.returncode == 0:
        print("\n✓ Fix command completed successfully!")
    else:
        print("\n✗ Fix command failed. Check output file for details.")
        
except Exception as e:
    print(f"Error running command: {e}")
    sys.exit(1)
