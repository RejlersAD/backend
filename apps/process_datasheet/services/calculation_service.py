"""
Calculation Service
Engineering calculation formulas for process equipment
"""
import math
from typing import Dict, Any, List
from decimal import Decimal


class CalculationService:
    """
    Engineering calculations for process datasheets
    Implements formulas defined in equipment configurations
    """
    
    @staticmethod
    def calculate_all(datasheet_data: Dict, equipment_config: Dict) -> Dict[str, Any]:
        """
        Execute all calculations for a datasheet
        
        Args:
            datasheet_data: Current datasheet data
            equipment_config: Equipment type configuration with calculation formulas
            
        Returns:
            Dictionary of calculated values with metadata
        """
        calculations = equipment_config.get('calculations', [])
        results = {}
        
        for calc in calculations:
            calc_id = calc.get('id')
            formula = calc.get('formula')
            
            try:
                result = CalculationService.execute_calculation(
                    formula=formula,
                    inputs=calc.get('inputs', []),
                    data=datasheet_data
                )
                
                results[calc_id] = {
                    'value': result,
                    'formula': formula,
                    'success': True,
                    'error': None
                }
            except Exception as e:
                results[calc_id] = {
                    'value': None,
                    'formula': formula,
                    'success': False,
                    'error': str(e)
                }
        
        return results
    
    @staticmethod
    def execute_calculation(formula: str, inputs: List[str], data: Dict) -> float:
        """
        Execute a specific calculation formula
        
        Args:
            formula: Formula identifier
            inputs: Required input field IDs
            data: Datasheet data
            
        Returns:
            Calculated value
        """
        # Extract input values from nested data structure
        input_values = {}
        for input_field in inputs:
            value = CalculationService._get_nested_value(data, input_field)
            if value is None:
                raise ValueError(f"Missing required input: {input_field}")
            input_values[input_field] = value
        
        # Route to specific calculation method
        if formula == 'liquid_cv':
            return CalculationService.calculate_liquid_cv(**input_values)
        elif formula == 'cavitation_sigma':
            return CalculationService.calculate_cavitation_index(**input_values)
        elif formula == 'iec_60534_noise':
            return CalculationService.calculate_noise_level(**input_values)
        else:
            raise ValueError(f"Unknown formula: {formula}")
    
    @staticmethod
    def _get_nested_value(data: Dict, field_path: str) -> Any:
        """
        Get value from nested dictionary using dot notation
        
        Args:
            data: Nested dictionary
            field_path: Path like 'section.field' or 'field'
            
        Returns:
            Field value or None
        """
        # Try to find in any section
        for section_name, section_data in data.items():
            if isinstance(section_data, dict):
                if field_path in section_data:
                    value = section_data[field_path]
                    # Extract value from value/unit structure
                    if isinstance(value, dict) and 'value' in value:
                        return float(value['value']) if value['value'] else None
                    return float(value) if value else None
        
        # Try direct access
        if field_path in data:
            value = data[field_path]
            if isinstance(value, dict) and 'value' in value:
                return float(value['value']) if value['value'] else None
            return float(value) if value else None
        
        return None
    
    # ==================== Control Valve Calculations ====================
    
    @staticmethod
    def calculate_liquid_cv(flow_rate_normal: float, density: float = None, 
                           pressure_drop: float = None, **kwargs) -> float:
        """
        Calculate Cv (Flow Coefficient) for liquid service
        
        Formula: Cv = Q * sqrt(SG / ΔP)
        Where:
            Q = Flow rate (m³/h)
            SG = Specific Gravity (relative to water)
            ΔP = Pressure drop (bar)
            
        Note: Formula uses standard units conversion factors
        """
        if not all([flow_rate_normal, pressure_drop]):
            raise ValueError("Missing required parameters for Cv calculation")
        
        # Default water density if not provided
        specific_gravity = density / 1000 if density else 1.0
        
        # Convert m³/h to GPM (US gallons per minute)
        flow_gpm = flow_rate_normal * 4.403
        
        # Convert bar to psi
        pressure_drop_psi = pressure_drop * 14.504
        
        # Cv calculation
        cv = flow_gpm * math.sqrt(specific_gravity / pressure_drop_psi)
        
        return round(cv, 2)
    
    @staticmethod
    def calculate_cavitation_index(pressure_operating: float, pressure_drop: float,
                                   vapor_pressure: float = None, **kwargs) -> float:
        """
        Calculate Cavitation Index (Sigma)
        
        Formula: σ = (P1 - Pv) / (P1 - P2)
        Where:
            P1 = Inlet pressure (bar abs)
            P2 = Outlet pressure (bar abs)
            Pv = Vapor pressure (bar abs)
            
        Sigma > 0.7 = No cavitation risk
        Sigma 0.5-0.7 = Moderate risk
        Sigma < 0.5 = High risk
        """
        if not all([pressure_operating, pressure_drop]):
            raise ValueError("Missing required parameters for cavitation calculation")
        
        # Default vapor pressure (water at 20°C) if not provided
        pv = vapor_pressure if vapor_pressure else 0.023
        
        # Convert to absolute pressure (assuming gauge pressure input)
        p1_abs = pressure_operating + 1.013
        p2_abs = p1_abs - pressure_drop
        
        if p1_abs <= p2_abs:
            raise ValueError("Inlet pressure must be greater than outlet pressure")
        
        sigma = (p1_abs - pv) / (p1_abs - p2_abs)
        
        return round(sigma, 3)
    
    @staticmethod
    def calculate_noise_level(cv_required: float, pressure_drop: float,
                             flow_rate_normal: float, valve_characteristic: str = 'equal_percentage',
                             **kwargs) -> float:
        """
        Calculate predicted noise level (IEC 60534-8-3)
        
        Simplified formula for estimation:
        SPL = 10 * log10(Cv * ΔP * Q)
        
        Where:
            Cv = Flow coefficient
            ΔP = Pressure drop (bar)
            Q = Flow rate (m³/h)
            
        Note: This is a simplified estimation. Full IEC calculation requires more parameters.
        """
        if not all([cv_required, pressure_drop, flow_rate_normal]):
            raise ValueError("Missing required parameters for noise calculation")
        
        # Simplified noise prediction (dBA)
        acoustic_power = cv_required * pressure_drop * (flow_rate_normal / 100)
        
        if acoustic_power <= 0:
            return 0.0
        
        spl = 10 * math.log10(acoustic_power) + 50  # Base noise level
        
        # Adjust for valve characteristic
        if valve_characteristic == 'equal_percentage':
            spl += 2
        elif valve_characteristic == 'quick_opening':
            spl += 5
        
        return round(min(spl, 120), 1)  # Cap at 120 dBA
    
    # ==================== Pump Calculations ====================
    
    @staticmethod
    def calculate_pump_power(flow_rate: float, head: float, efficiency: float = 0.75,
                            fluid_density: float = 1000, **kwargs) -> float:
        """
        Calculate pump shaft power
        
        Formula: P = (ρ * g * Q * H) / (η * 3600)
        Where:
            ρ = Density (kg/m³)
            g = Gravity (9.81 m/s²)
            Q = Flow rate (m³/h)
            H = Head (m)
            η = Efficiency (decimal)
            
        Returns: Power in kW
        """
        if not all([flow_rate, head]):
            raise ValueError("Missing required parameters for power calculation")
        
        power = (fluid_density * 9.81 * flow_rate * head) / (efficiency * 3600)
        
        return round(power, 2)
    
    @staticmethod
    def calculate_npsh_available(suction_pressure: float, vapor_pressure: float,
                                 suction_head: float, velocity_head: float = 0,
                                 **kwargs) -> float:
        """
        Calculate NPSH Available
        
        Formula: NPSHa = Ps + Pstatic - Pvp - Pfriction - Pvelocity
        Simplified: NPSHa = (Ps - Pvp) * 10.2 + Hs
        
        Where:
            Ps = Suction pressure (bar abs)
            Pvp = Vapor pressure (bar abs)
            Hs = Static suction head (m)
            
        Returns: NPSHa in meters
        """
        if not all([suction_pressure, vapor_pressure]):
            raise ValueError("Missing required parameters for NPSH calculation")
        
        npsh_a = (suction_pressure - vapor_pressure) * 10.2 + suction_head - velocity_head
        
        return round(npsh_a, 2)
    
    # ==================== Heat Exchanger Calculations ====================
    
    @staticmethod
    def calculate_lmtd(t1_hot_in: float, t1_hot_out: float,
                      t2_cold_in: float, t2_cold_out: float, **kwargs) -> float:
        """
        Calculate Log Mean Temperature Difference (LMTD)
        
        Formula: LMTD = (ΔT1 - ΔT2) / ln(ΔT1/ΔT2)
        Where:
            ΔT1 = T_hot_in - T_cold_out
            ΔT2 = T_hot_out - T_cold_in
        """
        dt1 = t1_hot_in - t2_cold_out
        dt2 = t1_hot_out - t2_cold_in
        
        if dt1 <= 0 or dt2 <= 0:
            raise ValueError("Invalid temperature profile for heat exchange")
        
        if abs(dt1 - dt2) < 0.1:
            return (dt1 + dt2) / 2
        
        lmtd = (dt1 - dt2) / math.log(dt1 / dt2)
        
        return round(lmtd, 2)
    
    @staticmethod
    def calculate_heat_duty(flow_rate: float, specific_heat: float,
                           temperature_in: float, temperature_out: float,
                           **kwargs) -> float:
        """
        Calculate heat duty
        
        Formula: Q = m * Cp * ΔT
        Where:
            m = Mass flow rate (kg/h)
            Cp = Specific heat (kJ/kg·K)
            ΔT = Temperature difference (K or °C)
            
        Returns: Heat duty in kW
        """
        if not all([flow_rate, specific_heat, temperature_in, temperature_out]):
            raise ValueError("Missing required parameters for heat duty calculation")
        
        temp_diff = abs(temperature_out - temperature_in)
        heat_duty = (flow_rate * specific_heat * temp_diff) / 3600
        
        return round(heat_duty, 2)
    
    # ==================== Pressure Vessel Calculations ====================
    
    @staticmethod
    def calculate_wall_thickness(diameter: float, pressure: float,
                                 allowable_stress: float, joint_efficiency: float = 1.0,
                                 corrosion_allowance: float = 3.0, **kwargs) -> float:
        """
        Calculate minimum wall thickness (ASME Section VIII Div 1)
        
        Formula: t = (P * R) / (S * E - 0.6 * P) + CA
        Where:
            P = Design pressure (bar)
            R = Inside radius (mm)
            S = Allowable stress (MPa)
            E = Joint efficiency
            CA = Corrosion allowance (mm)
        """
        if not all([diameter, pressure, allowable_stress]):
            raise ValueError("Missing required parameters for wall thickness calculation")
        
        radius = diameter / 2
        pressure_mpa = pressure / 10  # Convert bar to MPa
        
        t = (pressure_mpa * radius) / (allowable_stress * joint_efficiency - 0.6 * pressure_mpa)
        t_total = t + corrosion_allowance
        
        return round(t_total, 2)
    
    # ==================== Utility Methods ====================
    
    @staticmethod
    def validate_inputs(inputs: Dict[str, float], required: List[str]) -> None:
        """Validate that all required inputs are present and valid"""
        missing = [field for field in required if field not in inputs or inputs[field] is None]
        if missing:
            raise ValueError(f"Missing required inputs: {', '.join(missing)}")
        
        # Check for negative values where not allowed
        for field, value in inputs.items():
            if isinstance(value, (int, float)) and value < 0:
                if field not in ['suction_head', 'temperature_in', 'temperature_out']:
                    raise ValueError(f"Invalid negative value for {field}")
