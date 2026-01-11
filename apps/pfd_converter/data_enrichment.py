"""
DATA ENRICHMENT MODULE
======================
Add professional engineering defaults to sparse PFD data
"""

def enrich_equipment_data(equipment_list):
    """Add operating conditions, insulation, elevation, and nozzles"""
    enriched = []
    
    for eq in equipment_list:
        eq_copy = eq.copy()
        eq_type = eq.get('type', '').lower()
        tag = eq.get('tag', '')
        position = eq.get('position', {})
        
        # Add operating conditions based on equipment type
        if 'vessel' in eq_type or 'column' in eq_type:
            eq_copy.setdefault('operating_pressure', '10 barg')
            eq_copy.setdefault('design_pressure', '15 barg')
            eq_copy.setdefault('operating_temperature', '50°C')
            eq_copy.setdefault('design_temperature', '80°C')
            eq_copy.setdefault('material', 'CS')
            eq_copy.setdefault('insulation', 'hot')
            
        elif 'pump' in eq_type:
            eq_copy.setdefault('operating_pressure', '15 barg discharge')
            eq_copy.setdefault('design_pressure', '20 barg')
            eq_copy.setdefault('operating_temperature', '40°C')
            eq_copy.setdefault('material', 'CS')
            eq_copy.setdefault('insulation', 'none')
            
        elif 'tank' in eq_type:
            eq_copy.setdefault('operating_pressure', 'Atmospheric')
            eq_copy.setdefault('design_pressure', '0.5 barg')
            eq_copy.setdefault('operating_temperature', 'Ambient')
            eq_copy.setdefault('material', 'CS')
            eq_copy.setdefault('insulation', 'none')
        
        # Add elevation based on Y-position (Y * 10 meters)
        y_pos = position.get('y', 0.5)
        elevation_m = int(y_pos * 20)  # Scale factor
        eq_copy['elevation'] = f"EL. {elevation_m}m"
        
        enriched.append(eq_copy)
    
    return enriched


def enrich_stream_data(stream_list):
    """Add material, rating, pipe class defaults"""
    enriched = []
    
    for stream in stream_list:
        stream_copy = stream.copy()
        
        # Add defaults if missing
        stream_copy.setdefault('material', 'CS')
        stream_copy.setdefault('rating', '150#')
        stream_copy.setdefault('pipe_class', 'A1')
        
        # Generate line number if missing
        if not stream_copy.get('line_number'):
            stream_id = stream_copy.get('stream_id', '')
            source = stream_copy.get('source', stream_copy.get('from', ''))
            if source and '-' in source:
                # Extract unit number from source tag (e.g., 604-P-0101 -> 604)
                unit = source.split('-')[0]
                stream_copy['line_number'] = f"{unit}-PL-{stream_id:0>3}"
        
        enriched.append(stream_copy)
    
    return enriched


def enrich_instrument_data(instrument_list):
    """Add location type, service descriptions"""
    enriched = []
    
    for inst in instrument_list:
        inst_copy = inst.copy()
        
        # Determine location type from tag or type
        tag = inst.get('tag', '')
        inst_type = inst.get('type', '').lower()
        
        # Field mounted by default, control room if indicator/controller
        if 'indicator' in inst_type or 'controller' in inst_type:
            inst_copy.setdefault('location_type', 'control_room')
        else:
            inst_copy.setdefault('location_type', 'field')
        
        # Add service description from measured variable
        measured_var = inst.get('measured_variable', '')
        if measured_var and not inst_copy.get('service'):
            inst_copy['service'] = f"{measured_var.title()} Measurement"
        
        enriched.append(inst_copy)
    
    return enriched


def enrich_valve_data(valve_list, stream_list):
    """Associate valves with streams/lines"""
    enriched = []
    
    for valve in valve_list:
        valve_copy = valve.copy()
        
        # Try to associate valve with a stream based on size match
        valve_size = valve.get('size', '')
        for stream in stream_list:
            stream_size = stream.get('line_size', stream.get('size', ''))
            if valve_size in stream_size or stream_size in valve_size:
                valve_copy['stream_id'] = stream.get('stream_id')
                valve_copy['line_number'] = stream.get('line_number', '')
                break
        
        # Add actuator default
        valve_type = valve.get('type', '').lower()
        if 'control' in valve_type:
            valve_copy.setdefault('actuator', 'pneumatic')
            valve_copy.setdefault('fail_position', 'fail_close')
        else:
            valve_copy.setdefault('actuator', 'manual')
        
        enriched.append(valve_copy)
    
    return enriched


def enrich_all_data(specs):
    """Enrich all data in specs dictionary"""
    enriched_specs = specs.copy()
    
    # Enrich equipment
    equipment = specs.get('equipment', [])
    enriched_specs['equipment'] = enrich_equipment_data(equipment)
    
    # Enrich streams
    streams = specs.get('process_streams', specs.get('piping', []))
    enriched_specs['piping'] = enrich_stream_data(streams)
    enriched_specs['process_streams'] = enriched_specs['piping']
    
    # Enrich instruments
    instruments = specs.get('instruments', specs.get('instrumentation', []))
    enriched_specs['instruments'] = enrich_instrument_data(instruments)
    enriched_specs['instrumentation'] = enriched_specs['instruments']
    
    # Enrich valves
    valves = specs.get('valves', [])
    enriched_specs['valves'] = enrich_valve_data(valves, enriched_specs['piping'])
    
    return enriched_specs


# Example usage:
if __name__ == "__main__":
    import json
    
    test_specs = {
        'equipment': [
            {'tag': '604-V-0101', 'type': 'vessel', 'position': {'x': 0.3, 'y': 0.5}, 'description': 'Degasser'},
            {'tag': '604-P-0101A', 'type': 'pump', 'position': {'x': 0.1, 'y': 0.7}, 'description': 'Transfer Pump'},
        ],
        'process_streams': [
            {'phase': 'liquid', 'source': '604-P-0101A', 'line_size': '6 inch', 'stream_id': '1', 'destination': '604-V-0101'},
        ],
        'instruments': [
            {'tag': '604-PT-0101', 'type': 'pressure_transmitter', 'location': '604-V-0101', 'measured_variable': 'pressure'},
        ],
        'valves': [
            {'tag': '604-FCV-0101', 'size': '4 inch', 'type': 'flow_control_valve'},
        ]
    }
    
    enriched = enrich_all_data(test_specs)
    print(json.dumps(enriched, indent=2))
