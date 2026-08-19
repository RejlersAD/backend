"""
MOV Streams View - Generates HTML preview for MOV datasheets
"""
from typing import Dict


def generate_html_preview(mapped_data: Dict) -> str:
    """
    Generate HTML preview table for MOV datasheet
    Similar to SDV but with MOV-specific fields
    
    Args:
        mapped_data: Dictionary containing valve data
    
    Returns:
        HTML string with preview table
    """
    valves = mapped_data.get('valves', [])
    
    if not valves:
        return "<p>No MOV valves found in the data.</p>"
    
    # Build HTML table
    html = """
    <style>
        .mov-preview-table {
            width: 100%;
            border-collapse: collapse;
            margin: 20px 0;
            font-family: Arial, sans-serif;
            font-size: 12px;
        }
        .mov-preview-table th,
        .mov-preview-table td {
            border: 1px solid #ddd;
            padding: 8px;
            text-align: left;
        }
        .mov-preview-table th {
            background-color: #4CAF50;
            color: white;
            font-weight: bold;
        }
        .mov-section-header {
            background-color: #e8e8e8;
            font-weight: bold;
            text-align: center;
        }
        .mov-blank-section {
            background-color: #f9f9f9;
            color: #999;
            font-style: italic;
        }
    </style>
    
    <div style="overflow-x: auto;">
        <table class="mov-preview-table">
            <thead>
                <tr>
                    <th colspan="15" style="text-align: center; font-size: 16px;">PROCESS DATA SHEET - MOV (Motor Operated Valve)</th>
                </tr>
            </thead>
            <tbody>
    """
    
    for idx, valve in enumerate(valves):
        if idx > 0:
            html += '<tr><td colspan="15" style="height: 10px; background-color: #fff;"></td></tr>'
        
        # SECTION 1: GENERAL DATA
        html += f"""
                <tr>
                    <td colspan="15" class="mov-section-header">SECTION 1 - GENERAL DATA</td>
                </tr>
                <tr>
                    <td style="width: 3%;">1</td>
                    <td style="width: 15%;">Tag No</td>
                    <td colspan="13">{valve.get('tag_no', 'N/A')}</td>
                </tr>
                <tr>
                    <td>2</td>
                    <td>Service</td>
                    <td colspan="13">{valve.get('service', 'N/A')}</td>
                </tr>
                <tr>
                    <td>3</td>
                    <td>P&ID No.</td>
                    <td colspan="13">{valve.get('pid_no', 'N/A')}</td>
                </tr>
                <tr>
                    <td>4</td>
                    <td>Line No</td>
                    <td colspan="5">{valve.get('line_no', 'N/A')}</td>
                    <td colspan="2">Piping Class</td>
                    <td colspan="6">{valve.get('piping_class', 'N/A')}</td>
                </tr>
                <tr>
                    <td>5</td>
                    <td>Fluid</td>
                    <td colspan="3">{valve.get('fluid', 'N/A')}</td>
                    <td colspan="2">State</td>
                    <td colspan="3">{valve.get('state', 'N/A')}</td>
                    <td>Phase</td>
                    <td colspan="5">{valve.get('phase', 'N/A')}</td>
                </tr>
        """
        
        # SECTION 2: OPERATING CONDITIONS
        html += f"""
                <tr>
                    <td colspan="15" class="mov-section-header">SECTION 2 - OPERATING CONDITIONS</td>
                </tr>
                <tr>
                    <td>6</td>
                    <td>Operating Pressure</td>
                    <td>Min</td>
                    <td>{valve.get('operating_pressure_min', 'N/A')}</td>
                    <td>Normal</td>
                    <td>{valve.get('operating_pressure_normal', 'N/A')}</td>
                    <td>Max</td>
                    <td>{valve.get('operating_pressure_max', 'N/A')}</td>
                    <td>Unit</td>
                    <td colspan="6">{valve.get('pressure_unit', 'N/A')}</td>
                </tr>
                <tr>
                    <td>7</td>
                    <td>Operating Temperature</td>
                    <td>Min</td>
                    <td>{valve.get('operating_temp_min', 'N/A')}</td>
                    <td>Normal</td>
                    <td>{valve.get('operating_temp_normal', 'N/A')}</td>
                    <td>Max</td>
                    <td>{valve.get('operating_temp_max', 'N/A')}</td>
                    <td>Unit</td>
                    <td colspan="6">{valve.get('operating_temp_unit', 'N/A')}</td>
                </tr>
                <tr>
                    <td>8</td>
                    <td>Design Pressure</td>
                    <td>Min</td>
                    <td colspan="4">{valve.get('design_pressure_min') or '0'}</td>
                    <td>Max</td>
                    <td colspan="7">{valve.get('design_pressure_max') or valve.get('design_pressure') or 'N/A'}</td>
                </tr>
                <tr>
                    <td>9</td>
                    <td>Design Temperature</td>
                    <td>Min</td>
                    <td colspan="4">{valve.get('design_temp_min') or valve.get('design_temp') or 'N/A'}</td>
                    <td>Max</td>
                    <td colspan="7">{valve.get('design_temp_max') or valve.get('design_temp') or 'N/A'}</td>
                </tr>
                <tr>
                    <td>10</td>
                    <td>Sour Service</td>
                    <td colspan="5">{valve.get('sour_service') or 'N/A'}</td>
                    <td colspan="2">Special Conditions</td>
                    <td colspan="6">{valve.get('special_conditions') or 'None'}</td>
                </tr>
                <tr>
                    <td>11</td>
                    <td>Shut Off Pressure</td>
                    <td colspan="13">{valve.get('shut_off_pressure', 'N/A')}</td>
                </tr>
        """
        
        # SECTION 3: VALVE DETAILS (BLANK)
        html += f"""
                <tr>
                    <td colspan="15" class="mov-section-header">SECTION 3 - VALVE DETAILS (To be filled manually)</td>
                </tr>
                <tr class="mov-blank-section">
                    <td>12</td>
                    <td>Diff. Pressure (ΔP)</td>
                    <td colspan="13">(Blank - Manual Input Required)</td>
                </tr>
                <tr class="mov-blank-section">
                    <td>13</td>
                    <td>Seat Leakage Class</td>
                    <td colspan="5">(Blank)</td>
                    <td colspan="2">NACE Compliant</td>
                    <td colspan="6">(Blank)</td>
                </tr>
        """
        
        # SECTION 4: ACTUATOR DETAILS (BLANK)
        html += f"""
                <tr>
                    <td colspan="15" class="mov-section-header">SECTION 4 - ACTUATOR DETAILS (To be filled manually)</td>
                </tr>
                <tr class="mov-blank-section">
                    <td>14</td>
                    <td>Fail Position</td>
                    <td colspan="13">(Blank - Manual Input Required)</td>
                </tr>
                <tr class="mov-blank-section">
                    <td>15</td>
                    <td>Valve Close Time</td>
                    <td colspan="5">(Blank)</td>
                    <td colspan="2">Valve Open Time</td>
                    <td colspan="6">(Blank)</td>
                </tr>
        """
    
    html += """
            </tbody>
        </table>
    </div>
    """
    
    return html
