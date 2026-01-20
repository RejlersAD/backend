#!/usr/bin/env python3
"""
Script to fix indentation issues in services.py caused by Unicode character removal
"""

def fix_services_indentation():
    print("Reading services.py...")
    
    # Read the file
    with open('apps/pid_analysis/services.py', 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    print(f"Total lines: {len(lines)}")
    
    # Find where the class starts and where the methods start
    class_start = None
    methods_start = None
    
    for i, line in enumerate(lines):
        if line.strip() == 'class PIDAnalysisService:':
            class_start = i
            print(f"Found class at line {i+1}")
        elif '__init__' in line and 'def' in line and class_start is not None:
            methods_start = i
            print(f"Found first method at line {i+1}")
            break
    
    if class_start is None or methods_start is None:
        print("Could not find class or method start")
        return
    
    # Process lines and fix indentation
    fixed_lines = []
    
    for i, line in enumerate(lines):
        if i <= class_start:
            # Keep everything before the class as-is
            fixed_lines.append(line)
        elif i < methods_start:
            # This is the class definition and class variables - ensure proper indentation
            stripped = line.strip()
            if stripped == '':
                fixed_lines.append('\n')
            elif stripped.startswith('"""') or stripped.startswith('#'):
                # Class docstring or comment
                fixed_lines.append('    ' + stripped + '\n')
            elif '=' in stripped and not stripped.startswith('def'):
                # Class variable
                fixed_lines.append('    ' + stripped + '\n')
            elif stripped.startswith('ANALYSIS_PROMPT'):
                # Special handling for the prompt
                fixed_lines.append('    ' + stripped + '\n')
            elif '"""' in stripped and not stripped.startswith('"""'):
                # Part of multi-line string
                fixed_lines.append(stripped + '\n')
            else:
                # Other class-level content
                fixed_lines.append('    ' + stripped + '\n')
        else:
            # From methods onwards - fix method-level indentation
            stripped = line.strip()
            if stripped == '':
                fixed_lines.append('\n')
            elif stripped.startswith('def '):
                # Method definition - 4 spaces from class
                fixed_lines.append('    ' + stripped + '\n')
            elif stripped.startswith('"""'):
                # Method docstring - 8 spaces from class
                fixed_lines.append('        ' + stripped + '\n')
            elif stripped.startswith('class '):
                # Nested class - 4 spaces from class
                fixed_lines.append('    ' + stripped + '\n')
            elif any(stripped.startswith(kw) for kw in ['if ', 'elif ', 'else:', 'for ', 'while ', 'try:', 'except ', 'finally:', 'with ', 'return ', 'raise ', 'print(', 'import ', 'from ']):
                # Control structures and statements - 8 spaces from class
                fixed_lines.append('        ' + stripped + '\n')
            elif stripped.startswith('#'):
                # Comments - 8 spaces from class
                fixed_lines.append('        ' + stripped + '\n')
            elif stripped and not line.startswith('        '):
                # Other code that needs to be indented - 8 spaces from class
                fixed_lines.append('        ' + stripped + '\n')
            else:
                # Keep line as-is if it's already properly indented
                fixed_lines.append(line)
    
    # Write the fixed file
    with open('apps/pid_analysis/services_fixed.py', 'w', encoding='utf-8') as f:
        f.writelines(fixed_lines)
    
    print("Created fixed file: apps/pid_analysis/services_fixed.py")

if __name__ == '__main__':
    fix_services_indentation()