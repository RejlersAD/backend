"""
P&ID Analysis Service
AI-powered P&ID verification using OpenAI GPT-4 Vision with RAG support
"""
import os
import base64
import io
import json
from datetime import datetime
from typing import Dict, List, Any, Optional
from django.conf import settings
from openai import OpenAI
import fitz # PyMuPDF
from PIL import Image
from .rag_service import RAGService


class PIDAnalysisService:
    """Enhanced AI-Powered P&ID Analysis Service with Intelligent Adaptation"""

    # Enhanced soft-coded configuration with intelligent defaults
    MIN_ISSUES_REQUIRED = int(os.getenv('PID_MIN_ISSUES', '10')) # Reduced for more realistic expectations
    STRICT_MIN_ISSUES = os.getenv('PID_STRICT_MIN_ISSUES', 'False').lower() in ('true', '1', 'yes')
    MAX_TOKENS = int(os.getenv('PID_MAX_TOKENS', '16000'))
    AI_TEMPERATURE = float(os.getenv('PID_AI_TEMPERATURE', '0.1')) # More deterministic
    ANALYSIS_DEPTH = os.getenv('PID_ANALYSIS_DEPTH', 'comprehensive')

    # New intelligent analysis parameters
    CONTEXT_WINDOW_SIZE = int(os.getenv('PID_CONTEXT_WINDOW', '4000')) # Context for RAG
    RETRY_ATTEMPTS = int(os.getenv('PID_RETRY_ATTEMPTS', '3')) # API call retries
    ANALYSIS_CONFIDENCE_THRESHOLD = float(os.getenv('PID_CONFIDENCE_THRESHOLD', '0.7')) # Quality threshold

    # Enhanced analysis prompt with better structure and intelligence
    ANALYSIS_PROMPT = """ROLE & CONTEXT

    You are an AI-Powered P&ID Design Verification Engine acting as a multidisciplinary Senior Engineering Team with deep expertise in:

    - **Process Engineering**: Material & energy balance, process safety, operating envelopes, process control philosophy
    - **Piping Engineering**: Pipe stress, hydraulics, material selection, insulation, heat tracing, pressure drop
    - **Instrumentation & Control**: Control loops, alarm management, SIS/SIL, BPCS, field instruments, DCS integration
    - **Mechanical Engineering**: Equipment design, pressure vessels (ASME VIII), rotating equipment (API 610/617/618)
    - **Safety Engineering**: HAZOP, LOPA, relief systems (API 520/521), fire & gas detection, emergency systems
    - **Standards Compliance**: ADNOC DEP, Shell DEP, Saudi Aramco, API, ASME, ISA, IEC, ISO, NFPA

    **CRITICAL INSTRUCTIONS - INTELLIGENT ANALYSIS FRAMEWORK:**
    1. **CONTEXT AWARENESS**: Understand the PROCESS FUNCTION - is this a separation system, compression system, heating/cooling, chemical injection, utility system?
    2. **HOLISTIC VERIFICATION**: Analyze drawing as an INTEGRATED SYSTEM - check mass balance, energy balance, control philosophy consistency
    3. **EXACT DATA EXTRACTION**: Read ALL visible text, tags, line numbers, equipment specifications, datasheets, and schedules
    4. **REAL ISSUES ONLY**: Identify SPECIFIC problems based on what you SEE - no generic placeholders
    5. **ENGINEERING JUDGMENT**: Apply practical engineering knowledge - check feasibility, operability, maintainability, safety
    6. **STANDARDS-BASED**: Verify against ADNOC DEP 30.20.10.13-Gen (P&ID), ISA-5.1 (Instruments), API 14C (Safety), ASME B31.3 (Piping)
    7. **SEVERITY CALIBRATION**: Use engineering judgment for severity - not all missing labels are critical, but blocked PSVs always are

    MANDATORY EXTRACTION FROM DRAWING (COMPREHENSIVE DATA MINING)

    BEFORE analyzing, you MUST perform INTELLIGENT data extraction:

    **Drawing Metadata & Identification:**
    - Drawing number (title block - check multiple locations if needed)
    - Drawing title and process unit description
    - Revision number, date, and revision description/notes
    - Project name, client, contractor, discipline
    - Drawing scale, sheet size, sheet number (e.g., 1 of 5)
    - Approval signatures and dates (if visible)
    - Referenced drawings (PFDs, other P&IDs, specs, standards)

    **Equipment Data Extraction (CRITICAL - ENGINEERING-GRADE DETAIL):**
    For EACH equipment item, extract:
    - Equipment tag (V-101, P-2001A/B, E-301, C-401, T-501, etc.)
    - Equipment type (vessel, pump, compressor, heat exchanger, tank, etc.)
    - Design pressure (operating + margin per API/ASME)
    - Design temperature (operating + margin)
    - Material of construction (CS, 316SS, Alloy 625, titanium, etc.)
    - Dimensions (diameter, height, length, T/T, T/L, nozzle elevations)
    - Capacity/volume/duty (flow rate, heat duty, horsepower, volume)
    - Nozzle schedule (size, rating, service, orientation, elevation)
    - Operating data (normal/max/min pressures, temperatures, flows)
    - Special requirements (NACE, PWHT, NDT, coating, lining)
    - Equipment status (existing, new, future, standby, spare)
    - **CROSS-CHECK**: Verify values are reasonable (e.g., vessel pressure > line pressure, pump discharge > suction)

    **Instrument Alarm & Trip Schedule (CRITICAL - SAFETY-FOCUSED EXTRACTION):**
    For EACH instrument, extract and VALIDATE:
    - Instrument tag (LT-101, PT-202, TT-303, FT-404, AT-505, etc.)
    - Instrument type (transmitter, switch, indicator, controller, analyzer, etc.)
    - Service/process variable measured
    - Measurement range with units (0-100%, 0-50 barg, -20 to 150C, etc.)
    - Alarm setpoints:
    * HH (High-High) / PAHH, LAHH, TAHH, FAHH + action taken
    * H (High) / PAH, LAH, TAH, FAH + action taken
    * L (Low) / PAL, LAL, TAL, FAL + action taken
    * LL (Low-Low) / PALL, LALL, TALL, FALL + action taken
    - Trip setpoints and interlock logic (e.g., PSHH-101 trips P-101A/B)
    - Fail-safe position: FC (Fail Close), FO (Fail Open), FL (Fail Lock), FI (Fail In-place)
    - Safety classification (SIS, SIL-1/2/3, BPCS, alarm only)
    - Signal type (4-20mA, HART, digital, pneumatic 3-15psi)
    - Power supply (24VDC, 120VAC, instrument air, etc.)
    - Connection size, type, material (1/2" NPT, flanged, welded, etc.)
    - Area classification (Zone 0/1/2, Div 1/2, safe area)
    - **ENGINEERING CHECK**: Verify alarm hierarchy (LL < L < Normal < H < HH), check trip setpoints are appropriate for process safety

    **Line Number & Sizing (CRITICAL - COMPLETE PIPING VERIFICATION):**
    For EACH piping line segment, extract:
    - Complete line number (format: SIZE"-SERVICE-SEQ.NO-SPEC, e.g., 6"-P1501-CS-150)
    * SIZE: Nominal pipe size in inches
    * SERVICE: Process code (P=Process, U=Utility, F=Fuel Gas, etc.)
    * SEQ.NO: Sequential line number
    * SPEC: Material/piping class (CS-150, SS-300, LTCS-600, etc.)
    - Pipe size consistency check throughout routing (no abrupt changes without reducers)
    - Material specification and pressure rating verification
    - Insulation type (cold INS, heat tracing HT, personnel protection, acoustic)
    - Fluid service and phase (liquid, gas, two-phase, steam, etc.)
    - Design pressure class (150#, 300#, 600#, 900#, 1500#, 2500#)
    - Special requirements (slope for drainage, venting high points, low point drains)
    - Flow direction arrows and consistency
    - Branch connections (tees, wyes, laterals) with proper reducers
    - Connection to equipment nozzles (check size compatibility)
    - **ENGINEERING CHECK**: Verify pipe size is adequate for flow rate, check velocity limits, pressure drop considerations

    **Control Loops & Instrumentation Verification (COMPREHENSIVE SYSTEM CHECK):**
    For EACH control loop, verify COMPLETENESS:
    - Sensor (e.g., FT-101 - Flow Transmitter on line 4"-P-1501-CS)
    - Transmitter signal to control system (4-20mA to DCS/PLC)
    - Controller logic (PID, on-off, split-range, cascade, ratio, override)
    - Final control element (FCV-101 - Flow Control Valve with actuator)
    - Valve actuator type (pneumatic, electric, hydraulic, motor-operated)
    - Fail action matches process safety requirements:
    * FC for flow to fired heater (safety - stop flow on failure)
    * FO for cooling water (safety - maintain cooling on failure)
    * FL for critical positions (e.g., partially open bypass)
    - Control valve sizing (Cv value if shown, line size compatibility)
    - Bypass valves for maintenance and manual operation
    - Block valves upstream/downstream of control valve
    - **ENGINEERING CHECK**: Verify complete control loop, check fail-safe logic, validate control strategy

    **Safety Systems & Pressure Relief (CRITICAL SAFETY VERIFICATION):**
    Extract and VALIDATE all safety devices:
    - **Pressure Safety Valves (PSVs)**:
    * Tag (PSV-101, PRV-201, etc.)
    * Set pressure (psig, barg, kPag - must be < MAWP)
    * Relieving capacity (flow rate at set pressure)
    * Orifice designation (API 520 - D, E, F, G, H, J, K, etc.)
    * Inlet/outlet sizes (e.g., 2" inlet 3" outlet)
    * Protected equipment (vessel, heat exchanger, line segment)
    * Discharge destination (flare header, safe location, atmosphere)
    * Isolation valves (CSC - Car-Sealed Closed under PSV, CSO - Car-Sealed Open)
    * Tail pipe routing and support
    * **CHECK**: Verify PSV is not blocked by valves, check capacity vs. relief scenario

    - **Emergency Shutdown (ESD) Valves**:
    * ESD valve locations and tags (XV-ESD-101)
    * Fail-safe position (typically FC for process isolation)
    * Activation logic (manual button, process interlock, F&G system)
    * Response time requirements
    * Testing/bypass provisions

    - **Blowdown & Depressuring**:
    * Blowdown valve locations (BDV-101)
    * Depressuring lines to flare/safe location
    * Isolation and drain points

    - **Fire & Gas Detection**:
    * Gas detector locations (GD-101)
    * Fire detectors (heat, flame, smoke)
    * Shutdown actions on detection

    - **Interlocks & Permissives**:
    * Shutdown interlocks (e.g., Low-Low level in V-101 trip P-102)
    * Startup permissives (e.g., cannot start pump unless suction valve open)
    * Sequence logic for equipment startup/shutdown

    **Piping & Valve Details (COMPLETE MECHANICAL VERIFICATION):**
    Extract ALL valve information:
    - Isolation valves (gate, globe, ball valves) with size and type
    - Check valves (swing, lift, wafer) - verify correct orientation
    - Control valves (globe, ball, butterfly) with actuator type
    - Safety valves (PSV, PRV, PVRV) with set pressure
    - Special valves (3-way, needle, plug, diaphragm)
    - Manual/automated operation indication
    - Locked open (LO), locked closed (LC), car-sealed open (CSO), car-sealed closed (CSC)
    - Normally open (NO), normally closed (NC)
    - **ENGINEERING CHECK**: Verify check valve orientation (flow direction), verify isolation valve locations for maintenance

    **PID/PFD Development Guidelines Compliance (ADNOC DEP 30.20.10.13-Gen FOCUSED):**
    Check compliance with industry best practices:
    - **Legend & Symbol Compliance**:
    * All symbols match drawing legend
    * ISA-5.1-2009 instrument symbols used correctly
    * Line symbols match piping spec
    * Valve symbols are standard (gate, globe, ball, etc.)

    - **Numbering Conventions**:
    * Equipment numbering follows project standard (e.g., Area-Type-Sequence)
    * Line numbering follows company standard
    * Instrument tag numbering per ISA-5.1 (First letter = variable, suffix letters = function)

    - **Drawing Organization**:
    * Title block complete with all required information
    * Revision block with dates and descriptions
    * Notes and legends clear and complete
    * Process flow left-to-right or top-to-bottom
    * Equipment arranged logically

    - **Cross-References**:
    * References to PFDs (Process Flow Diagrams)
    * References to other P&IDs (continuation sheets)
    * References to specifications (piping spec, instrument spec)
    * References to standards (API, ASME, ISA, etc.)

    - **Completeness**:
    * All equipment shown with datasheets/specifications
    * All instruments shown with ranges and alarm/trip setpoints
    * All lines numbered and sized
    * Utility connections identified (cooling water, instrument air, nitrogen, steam, etc.)
    * Electrical classifications marked (Zone 0/1/2, Class I Div 1/2)

    SCOPE OF VERIFICATION (COMPREHENSIVE ENGINEERING REVIEW)

    Perform INTELLIGENT, STANDARDS-BASED analysis covering all engineering disciplines:

    **1 EQUIPMENT DATASHEET & MECHANICAL INTEGRITY VERIFICATION**
    For EACH equipment item, perform engineering validation:
    - **Tag Verification**: Extract exact tag from drawing (e.g., V-3610-01, P-2001-A, C-1501)
    - **Design Pressures**: Verify design pressure > operating pressure with appropriate margin (typically 10% minimum or per ASME VIII)
    - **Design Temperatures**: Check design temperature envelope covers operating range + margin
    - **Material Selection**: Validate MOC is suitable for service (corrosion, temperature, pressure)
    * Check for NACE MR0175 compliance for sour service (H2S)
    * Verify low-temperature carbon steel (LTCS) for < -20C service
    * Check stainless steel grade for corrosive services
    - **Nozzle Schedule Validation**:
    * Verify nozzle sizes match connected line sizes (or note reducers)
    * Check nozzle ratings match line pressure class
    * Validate orientation (vertical, horizontal, tangent vs. radial)
    - **Capacity Verification**:
    * For vessels: Check volume vs. residence time requirement
    * For pumps: Verify flow rate and head match process needs
    * For compressors: Check capacity, discharge pressure, power requirement
    * For heat exchangers: Validate heat duty, surface area, LMTD
    - **ASME Code Compliance**:
    * Vessels: ASME VIII Div 1/2, check code stamp requirement
    * Piping: ASME B31.3 Process Piping compliance
    - **Missing Data Identification**:
    * Flag equipment boxes with incomplete specifications
    * Note "TBC" (To Be Confirmed) or "TBD" (To Be Determined) items
    - **Engineering Checks**:
    * Verify equipment is properly sized (not oversized/undersized)
    * Check for venting and draining provisions
    * Verify accessibility for maintenance
    * Check support and foundation requirements noted

    **2 INSTRUMENT ALARM & TRIP SCHEDULE - SAFETY CRITICAL ANALYSIS**
    For EACH instrument, perform RIGOROUS verification:
    - **Tag Extraction**: Get exact instrument tag (e.g., LT-3610-01A, PT-2201, TT-5501)
    - **Instrument Type Classification**:
    * Transmitters (LT, PT, TT, FT, AT - for continuous measurement)
    * Switches (LSH/LSL, PSH/PSL, TSH/TSL, FSH/FSL - for discrete alarms/trips)
    * Indicators (LI, PI, TI, FI - local display)
    * Controllers (LIC, PIC, TIC, FIC - control loops)
    * Analyzers (AT - composition, pH, conductivity, etc.)
    - **Measurement Range Validation**:
    * Check range covers operating envelope (min to max values)
    * Verify range allows for transients and upsets
    * Example: Tank level 0-100% should cover empty to overflow
    - **Alarm Setpoint Validation (CRITICAL FOR PROCESS SAFETY)**:
    * Extract ALL alarm setpoints visible on drawing
    * **Verify alarm hierarchy**: LL < L < Normal Operating < H < HH
    * Check alarm values are appropriate for process (not too tight/too wide)
    * Example:
    - LIT-101: LL=15%, L=20%, H=80%, HH=90% (vessel level)
    - PIT-201: LL=5 barg, L=8 barg, H=25 barg, HH=28 barg (design=30 barg)
    * **Flag inconsistencies**: e.g., alarm setpoint > design pressure
    - **Trip Interlock Validation**:
    * Extract trip logic from drawing notes/interlock diagrams
    * Example: "LALL-101 trips P-201A/B and closes FCV-101"
    * Verify trip setpoints are conservative (activate before reaching dangerous conditions)
    * Check redundancy (e.g., 2oo3 voting for SIS)
    - **Fail-Safe Position Verification (CRITICAL)**:
    * **FC (Fail Close)**: Used when failure should STOP flow (e.g., fuel gas to heater)
    * **FO (Fail Open)**: Used when failure should MAINTAIN flow (e.g., cooling water to exchanger)
    * **FL (Fail Lock)**: Valve holds position on failure (critical for some processes)
    * **VALIDATE LOGIC**: Check fail action makes the process SAFER
    * Example: Cooling water valve should be FO (keep cooling on air failure)
    - **Safety Classification Verification**:
    * Identify SIS (Safety Instrumented System) loops - typically shown with special symbols
    * Check SIL rating (SIL-1, SIL-2, SIL-3) if indicated
    * Verify BPCS (Basic Process Control System) vs. SIS separation
    - **Signal & Power Supply Check**:
    * 4-20mA analog (most common) - check wiring diagram reference
    * HART (Highway Addressable Remote Transducer) - digital overlay on 4-20mA
    * Instrument air supply (typically 20-30 psig, clean and dry)
    * Electrical power (24VDC, 120VAC, 240VAC) - check hazardous area rating
    - **Installation Details**:
    * Check instrument location is accessible for maintenance
    * Verify instrument connection size (1/2", 3/4", 1", flanged, etc.)
    * Check for isolation valves, drain/vent valves around instruments
    * Verify installation in correct orientation (e.g., flow meter orientation)
    - **Missing Instrument Detection**:
    * Flag equipment without required instrumentation:
    - Pumps without discharge pressure indicator (mandatory per API 610)
    - Vessels without level indication
    - Heat exchangers without outlet temperature monitoring
    - Compressors without discharge pressure and temperature (API 617/618)

    **3 CONTROL LOOP VERIFICATION - CLOSED-LOOP VALIDATION**
    For EACH control loop, verify COMPLETENESS and LOGIC:
    - **Loop Identification**: Identify complete loops (sensor transmitter controller valve)
    - **Loop Components Check**:
    1. **Sensor/Primary Element**: Orifice plate, venturi, RTD, thermocouple, level probe, etc.
    2. **Transmitter**: FT, LT, PT, TT - converts sensor signal to standard output
    3. **Controller**: FIC, LIC, PIC, TIC - processes signal and generates control action
    4. **Final Control Element**: FCV, LCV, PCV, TCV - executes control action
    - **Control Strategy Validation**:
    * **Simple PID Control**: Most common (e.g., FIC-101 controls FCV-101)
    * **Cascade Control**: One controller output feeds setpoint to another (e.g., TIC FIC)
    * **Ratio Control**: Maintain ratio between two flows (e.g., fuel/air ratio)
    * **Split-Range Control**: One controller operates multiple valves (e.g., heating + cooling)
    * **Override Control**: Safety override takes precedence (e.g., high temperature overrides flow)
    - **Control Valve Validation**:
    * Check valve type suitable for service (globe, ball, butterfly, segmented ball, etc.)
    * Verify valve size (typically 1/2 to 2/3 of line size for good control range)
    * Check actuator type (pneumatic, electric, hydraulic, motor-operated)
    * Validate fail action (FC/FO) matches process safety requirement
    * Check for bypass line (for maintenance and manual operation during control valve failure)
    * Verify block valves (hand valves upstream/downstream) for isolation during maintenance
    - **Tuning & Performance**:
    * Check if controller tuning parameters are shown (Kp, Ti, Td)
    * Verify control range is appropriate (not too sensitive, not too sluggish)
    - **Bypass & Manual Operation**:
    * Verify bypass line around control valve for manual operation
    * Check hand valves for isolating control valve during maintenance
    * Ensure manual operation is possible without full shutdown

    **4 LINE NUMBER, SIZING & HYDRAULIC VERIFICATION**
    For EACH piping line segment, perform detailed piping engineering check:
    - **Line Number Extraction & Parsing**:
    * Format: SIZE"-SERVICE-SEQ.NO-SPEC
    * Example: 6"-P1501-CS-150
    - 6" = Nominal pipe size
    - P = Process (or U=Utility, F=Fuel, CW=Cooling Water, etc.)
    - 1501 = Sequential line number (area + sequence)
    - CS = Carbon Steel material
    - 150 = 150# pressure class (ASME B16.5)
    - **Pipe Sizing Validation (ENGINEERING CALCULATION SENSE-CHECK)**:
    * Verify pipe size is reasonable for flow rate:
    - Liquid lines: Velocity typically 1-3 m/s (up to 6 m/s for water)
    - Gas lines: Velocity typically 15-30 m/s (limit noise, erosion)
    - Two-phase flow: Special sizing required
    * Check for pressure drop concerns (long runs, high flow, high viscosity)
    * Flag undersized lines (high velocity erosion, noise, vibration)
    * Flag oversized lines (high cost, poor drainage, slug flow issues)
    - **Material Specification Validation**:
    * **Carbon Steel (CS, A106 Gr.B)**: Most common, -20C to +400C, non-corrosive service
    * **Stainless Steel (SS316, SS304)**: Corrosive service, high purity required
    * **Low Temp Carbon Steel (LTCS, A333 Gr.6)**: < -20C service (cryogenic)
    * **Alloy (Alloy 20, 625, Hastelloy)**: Highly corrosive service (acids, chlorides)
    * **Check NACE MR0175**: Sour service (H2S > 50 ppm) requires special materials
    - **Pressure Class Verification**:
    * Check pressure class matches or exceeds design pressure:
    - 150# (ANSI 150): Max pressure ~20 barg at ambient temp
    - 300# (ANSI 300): Max pressure ~50 barg at ambient temp
    - 600# (ANSI 600): Max pressure ~100 barg at ambient temp
    * Verify consistent pressure class through system (or note reducers)
    * Flag under-rated flanges or fittings
    - **Insulation & Heat Tracing**:
    * **Cold Insulation (CI)**: Prevent condensation, maintain low temperature
    * **Hot Insulation (HI)**: Conserve heat, personnel protection, freeze protection
    * **Heat Tracing (HT)**: Electric or steam tracing to prevent freezing
    * **Acoustic Insulation (AI)**: Noise reduction for high-pressure gas letdown
    * Check insulation is noted on drawing where required
    - **Slope, Venting & Draining**:
    * Verify slope notation for gravity drainage (typically 1:100 or 1:200)
    * Check high point vents (air/gas release during filling)
    * Check low point drains (liquid removal during draining, winterization)
    * Verify proper drainage to closed drain or sewer
    - **Flow Direction Verification**:
    * Check all lines have flow direction arrows
    * Verify flow direction is consistent with process logic
    * Check check valve orientation matches flow direction
    - **Pipe Specialty Items**:
    * Strainers upstream of pumps, control valves, instruments
    * Expansion loops or expansion joints for thermal expansion
    * Supports, guides, anchors noted for stress analysis
    * Steam traps on steam lines
    * Spectacle blinds for positive isolation

    **4 SPECIFICATION BREAKS (SPEC BREAKS) - CRITICAL IDENTIFICATION**
    **SPEC BREAKS are locations where piping specification changes (material, pressure class, or special requirements).**

    **Intelligent Spec Break Detection:**
    - **Scan ALL Pipe Lines** for specification changes:
    * **Material Changes**: CS SS316, CS Alloy, SS LTCS, etc.
    * **Pressure Class Changes**: 150# 300#, 300# 600#, etc.
    * **Special Requirement Changes**: Standard NACE, Non-traced Heat Traced, etc.

    - **Identify Each Spec Break Location**:
    * Note exact location (between which equipment, at which point on line)
    * Extract upstream line spec (line number, material, pressure class)
    * Extract downstream line spec (line number, material, pressure class)
    * Determine reason for break (corrosion, pressure, temperature, cost optimization)

    - **Verify Spec Break Documentation**:
    * Is spec break clearly marked on drawing? (Symbol, notation, callout)
    * Are both upstream and downstream line numbers shown?
    * Is transition piece specified? (Reducer, flange adapter, spool piece)
    * Are material specifications compatible? (dissimilar metal issues)
    * Is pressure rating adequate on both sides?

    - **Common Spec Break Scenarios**:
    * **CS to SS**: Entering corrosive service (acids, high chloride, sour gas)
    * **150# to 300#**: Pressure increase (pump discharge, compressor outlet)
    * **Standard to NACE**: Entering sour service (H2S > 50 ppm)
    * **Non-traced to Heat Traced**: Freeze protection zone, viscosity management
    * **Standard to Jacketed**: Temperature control requirement

    - **Flag Missing or Improper Spec Breaks**:
    * Spec break exists but not marked MAJOR issue (procurement/construction error risk)
    * Material incompatibility at break CRITICAL issue (galvanic corrosion)
    * Pressure class downgrade without justification CRITICAL issue (safety risk)
    * Missing transition piece specification MAJOR issue (installation delay)

    - **Procurement & Cost Impact**:
    * Note material cost differential (SS316 ~3x CS, Alloy ~10x CS)
    * Identify long-lead items (exotic materials, special flanges)
    * Flag impact on material take-off (MTO) and Bill of Materials (BOM)

    **Example Spec Breaks to Identify:**
    1. Line 6"-P-1501-CS-150 6"-P-1502-SS316-300 (before corrosive reactor feed)
    2. Line 4"-U-2101-CS-150 4"-U-2102-CS-300 (pump discharge pressure rise)
    3. Line 2"-P-3001-CS-150 2"-P-3002-LTCS-150 (entering cryogenic section)
    4. Line 8"-FG-4001-CS-150 8"-FG-4002-CS-150-NACE (entering sour gas area)

    **5 PID/PFD GUIDELINES COMPLIANCE - ADNOC DEP 30.20.10.13-Gen AUDIT**
    Comprehensive standards compliance check:
    - **Drawing Legend & Symbol Compliance**:
    * All symbols used match the drawing legend
    * ISA-5.1-2009 instrument symbols used correctly:
    - Filled circle = Board/DCS mounted (main control room)
    - Empty circle = Field mounted (on equipment/pipe)
    - Hexagon = Computer function (DCS/PLC logic)
    - Diamond = Shared display/control (operator interface)
    * Valve symbols match standard (gate, globe, ball, butterfly, check, control)
    * Equipment symbols match standard (vessel, column, tank, pump, compressor, etc.)
    - **Equipment Numbering Convention Verification**:
    * Check numbering follows project standard (area-type-sequence)
    * Example: V-3610-01 = Vessel, Area 36, Unit 10, Sequence 01
    * Verify consistency across drawing and with other P&IDs
    * Check for duplicate tags (CRITICAL ERROR)
    - **Instrument Tag Numbering per ISA-5.1**:
    * First letter = Process variable (L=Level, P=Pressure, T=Temperature, F=Flow, A=Analysis)
    * Subsequent letters = Function (I=Indicator, T=Transmitter, C=Controller, S=Switch)
    * Suffix letters/numbers = Loop identifier and sequence
    * Example: FIC-2501-A = Flow Indicator Controller, Loop 2501, Train A
    * **Validate Consistency**: FT-101, FIC-101, FCV-101 should be same flow loop
    - **Notes & Documentation**:
    * Check general notes are clear and complete
    * Verify all abbreviations are explained or standard
    * Check for design conditions noted (pressure, temperature, flow rates)
    * Verify references to specifications (piping spec, instrument spec, electrical area class)
    - **Cross-References to Other Documents**:
    * PFD (Process Flow Diagram) - overall process flowsheet
    * Other P&IDs (upstream/downstream tie-ins, continuation sheets)
    * Piping Specifications (piping class, material specs)
    * Instrument Specifications (instrument datasheets)
    * Electrical Area Classification drawings
    * Cause & Effect diagrams (shutdown logic)
    - **Drawing Completeness**:
    * Title block 100% complete (drawing number, title, rev, date, approvals)
    * Revision block with all revisions, dates, descriptions
    * Equipment list/schedule if shown
    * Instrument list if shown
    * Line list if shown
    * Utility connections identified (CW supply/return, IA, N2, steam, etc.)

    **6 SAFETY SYSTEMS & PRESSURE RELIEF - CRITICAL SAFETY VERIFICATION**
    HIGHEST PRIORITY - Process Safety Management:
    - **Pressure Safety Valve (PSV) Critical Checks**:
    * Extract ALL PSV tags, set pressures, capacities, orifice sizes
    * **CRITICAL**: Verify PSV set pressure MAWP (Maximum Allowable Working Pressure)
    * Check PSV is NOT blocked by isolation valves (CSO car-sealed-open only)
    * Verify discharge piping to safe location (flare header, atmosphere with safe dispersal)
    * Check for tail pipe support and proper routing (no pockets for liquid accumulation)
    * Validate isolation valves are CSC (car-sealed-closed) under PSV, CSO (car-sealed-open) in discharge
    * Verify PSV capacity covers worst-case relief scenario (fire, blocked outlet, runaway reaction)
    * Check orifice designation per API 520 (D, E, F, G, H, J, K, L, M, N, P, Q, R, T)
    - **Emergency Shutdown (ESD) System Verification**:
    * Identify all ESD valves (XV-ESD-101, etc.)
    * Check fail-safe position (typically FC for process isolation)
    * Verify ESD activation logic (F&G detection, manual ESD push button, process interlock)
    * Check ESD valve locations provide effective isolation for depressuring
    * Verify ESD valves are not bypassed (no open bypass line around ESD valve)
    - **Blowdown & Depressuring System**:
    * Check blowdown valves (BDV) to flare/safe location
    * Verify depressuring capacity for emergency scenarios
    * Check for low point drains and high point vents in blowdown system
    - **Interlock & Trip Logic Validation**:
    * Extract all shutdown interlocks visible on drawing or in notes
    * Example: "LALL-101 (<15%) trips P-201A/B and closes XV-101"
    * Verify interlock logic is safe and effective
    * Check for permissives (e.g., cannot start pump unless suction valve open)
    * Validate trip setpoints are conservative (trip before dangerous condition)
    - **Fire & Gas (F&G) System**:
    * Check gas detector locations (GD-101, toxic gas, flammable gas)
    * Check fire detector locations (flame, heat, smoke detectors)
    * Verify F&G activation triggers appropriate shutdowns (ESD valves, deluge systems)
    * Check for manual call points (manual fire alarm activation)
    - **Overpressure Protection Scenarios**:
    * Check ALL pressure vessels have PSV or rupture disc
    * Verify heat exchangers have thermal relief if isolation valves can trap liquid
    * Check blocked discharge scenarios (pump dead-head protection)
    * Verify PSV downstream of pressure reducing valve (PRV)

    **7 PROCESS ENGINEERING & OPERABILITY REVIEW**
    Apply engineering judgment and process knowledge:
    - **Material Balance Check**:
    * Verify inlet flows = outlet flows (accounting for reactions, venting, etc.)
    * Check for missing streams (vents, drains, relief streams)
    - **Energy Balance Sense-Check**:
    * Heat exchangers: Verify heating/cooling medium is appropriate
    * Fired heaters: Check fuel gas supply and combustion air
    * Refrigeration: Verify refrigerant supply and return
    - **Process Conditions Validation**:
    * Check operating pressure/temperature are within equipment design limits
    * Verify phase of fluid (liquid, gas, two-phase) is compatible with equipment design
    * Check for condensation/vaporization zones
    - **Operability Review**:
    * Verify startup/shutdown procedures are feasible
    * Check for dead legs (stagnant piping - corrosion/fouling risk)
    * Validate sample point locations for process monitoring
    * Check cleaning connections (steam-out, flush, chemical cleaning)
    - **Maintenance Accessibility**:
    * Verify isolation valves allow equipment maintenance without unit shutdown
    * Check for spectacle blinds or double block & bleed for positive isolation
    * Validate drain and vent points for equipment isolations
    * Check spare equipment in service (e.g., pumps, filters, columns)

    **8 UTILITY & AUXILIARY SYSTEMS CHECK**
    Verify all utility connections are shown:
    - **Instrument Air (IA)**:
    * Check IA supply to pneumatic instruments and control valves
    * Verify IA pressure is adequate (typically 5-7 barg)
    * Check for IA dryers/filters if required for instrument quality
    - **Nitrogen (N2)**:
    * Check N2 for purging, blanketing, pneumatic instruments in hazardous area
    * Verify N2 pressure is suitable for application
    - **Cooling Water (CW)**:
    * Check CW supply and return to heat exchangers, coolers, condensers
    * Verify CW isolation valves and control valves
    * Check for strainers and temperature monitoring
    - **Steam**:
    * Check steam supply to heat exchangers, steam tracing, ejectors
    * Verify steam pressure/temperature class (LP, MP, HP steam)
    * Check for steam traps and condensate return
    - **Electrical Power**:
    * Check motor-operated valves (MOV) have power supply noted
    * Verify electrical area classification (Zone 0/1/2, Div 1/2)
    * Check for emergency power (UPS, generator) for critical equipment
    - **Drain & Vent Systems**:
    * Check for closed drain system (contaminated/flammable liquids)
    * Check for open drain system (clean water, condensate)
    * Verify flare system for pressure relief and blowdown
    * Check vent stack for atmospheric venting (non-hazardous)

    **9 DOCUMENTATION, NOTES & COMPLETENESS AUDIT**
    Final completeness check:
    - **Title Block Verification**:
    * Drawing number complete and correct format
    * Drawing title describes the process unit/system
    * Revision number and date current
    * All approval signatures and dates present (or marked for approval)
    * Project name, client, contractor identified
    - **Revision Management**:
    * Revision block with complete history (rev, date, description, by, checked, approved)
    * Revision clouds on drawing highlighting changes (if not initial revision)
    * Notes reference revision number for clarity
    - **Notes & General Requirements**:
    * General notes are clear, complete, and unambiguous
    * Design conditions specified (design pressure, design temperature, MAWP, MDMT)
    * Fluid service, composition, phase clearly noted
    * Special requirements noted (NACE, PWHT, radiography, hydrotest pressure, etc.)
    - **References to Specifications & Standards**:
    * Piping material specifications (e.g., "Piping per spec 001-PS-101")
    * Instrument specifications (e.g., "Instruments per spec 001-IS-101")
    * Electrical area classification (e.g., "Electrical area class per drawing 001-EC-101")
    * Design codes (ASME, API, ISA, etc.)
    - **Legends & Symbols**:
    * All symbols used on drawing are explained in legend
    * Abbreviations are explained or are standard
    * Line designation, valve symbols, instrument symbols clearly defined
    - **HOLDS & NOTES Verification (CRITICAL - RIGHT TOP CORNER)**:
    * **HOLDS**: Extract ALL HOLD items listed on drawing (typically in table format at right top corner)
    - HOLD number/identifier
    - HOLD description/requirement
    - Check if drawing design complies with each HOLD
    - Flag violations of HOLD requirements as CRITICAL issues
    * **GENERAL NOTES**: Extract and verify ALL general notes (numbered notes section)
    - Design conditions and requirements
    - Material selection criteria
    - Special requirements (NACE, PWHT, testing, etc.)
    - Operating constraints and limitations
    * **DESIGN NOTES**: Extract project-specific design notes
    - Pressure/temperature design basis
    - Fluid properties and composition
    - Safety factors and margins
    - Special operating conditions
    * **Verify Compliance**: Check that drawing design follows ALL notes and HOLDS
    - Cross-reference equipment specs against notes
    - Verify instrument ranges match design conditions in notes
    - Confirm material selections comply with NACE/corrosion notes
    - Flag any violations of notes/holds as issues
    - **TBC (To Be Confirmed) Items**:
    * Identify all "TBC", "TBD", "TO BE CONFIRMED", "VERIFY" items on drawing
    * Flag these as requiring resolution before construction
    - **Cross-References Completeness**:
    * Check for continuation symbols (to/from other P&ID sheets)
    * Verify tie-in points to other systems are clearly marked
    * Check equipment references match equipment list/datasheets

    ANALYSIS QUALITY REQUIREMENTS (MANDATORY STANDARDS)

    **You MUST deliver ENGINEERING-GRADE analysis:**
    **Specificity**: Reference EXACT tags, values, line numbers from THIS drawing (no generic comments)
    **Completeness**: Extract ALL equipment datasheets, instrument schedules, line lists visible on drawing
    **Engineering Validation**: Apply engineering judgment - check calculations sense, safety logic, operability
    **Standards Compliance**: Reference specific standards (ADNOC DEP, API, ISA, ASME) when identifying issues
    **Severity Calibration**: Use engineering judgment:
    - **CRITICAL**: Immediate safety risk (blocked PSV, wrong pressure rating, missing ESD, SIS failure mode incorrect)
    - **MAJOR**: Operational failure risk (undersized line, missing instrument, incomplete control loop, wrong material)
    - **MINOR**: Documentation issue (missing note, unclear label, inconsistent numbering)
    - **OBSERVATION**: Improvement suggestion (add drain valve, consider redundancy, optimize routing)
    **Actionable Recommendations**: Provide SPECIFIC corrective actions (not "check this" but "change PSV-101 set pressure from 35 barg to 32 barg per vessel MAWP")
    **Cross-Referencing**: Link related issues (e.g., if PSV is blocked, also note no alternate protection)
    **Practical Engineering**: Consider constructability, operability, maintainability (not just code compliance)
    **Real Issues Only**: Do NOT generate placeholder/generic issues - if no issues found in a category, say so

    **You MUST NOT do the following:**
    Generate generic issues without specific tag/value references
    Skip equipment datasheet, instrument schedule, line list extraction (these are MANDATORY)
    Assume values not visible on drawing (if not visible, note "not visible on drawing")
    Provide vague recommendations ("review design" instead: "increase line size from 2" to 3" to reduce velocity from 8 m/s to 3.5 m/s")
    Over-flag minor formatting issues as CRITICAL (save CRITICAL for safety risks)
    Copy-paste same issue multiple times (consolidate similar issues)
    Ignore engineering context (understand the PROCESS before flagging "issues")

    EXPECTED ANALYSIS DEPTH (QUANTITY GUIDANCE)
    For a typical P&ID drawing (not blank, not simple utility), expect to find:
    - **Equipment Datasheets**: 5-20 equipment items (vessels, pumps, exchangers, etc.)
    - **Instrument Schedule**: 15-50 instruments (transmitters, switches, indicators, controllers)
    - **Line List**: 20-100 piping lines (depending on complexity)
    - **Issues Identified**: 15-50 observations (mix of critical/major/minor/observation)
    * If you find < 10 issues on a complex drawing, you likely MISSED something
    * If you find > 100 issues, you may be over-flagging trivial items
    - **PFD Guidelines Compliance**: Comprehensive check of legend, symbols, numbering, notes, references

    OUTPUT FORMAT (MANDATORY - COMPREHENSIVE JSON STRUCTURE)

    Return ONLY a valid JSON object with ALL sections populated based on actual drawing content:

    ```json
    {
    "drawing_info": {
    "drawing_number": "EXACT number from title block (e.g., 001-P&ID-2501-Rev.C)",
    "drawing_title": "EXACT title from drawing (e.g., Compressor Suction Drum & Antisurge System)",
    "revision": "EXACT revision (e.g., C, Rev.3, Rev.B)",
    "revision_date": "Revision date if visible (YYYY-MM-DD)",
    "project_name": "Project name/client from title block",
    "drawing_scale": "Scale if shown (e.g., 1:50, NTS)",
    "sheet_number": "Sheet number (e.g., 1 of 3)",
    "analysis_date": "Current date in ISO format (YYYY-MM-DD)",
    "process_unit": "Process unit description (e.g., Area 36 - Gas Compression)",
    "contractor": "Engineering contractor if shown",
    "design_basis": "Design codes/standards noted on drawing"
    },

    "equipment_datasheets": [
    {
    "equipment_tag": "V-3610-01",
    "equipment_type": "Vertical Separator / Pump / Heat Exchanger / Compressor / Tank / Column / Etc.",
    "service": "Process service description (e.g., HP Gas-Liquid Separator)",
    "design_pressure": "Value with unit (e.g., 45 barg)",
    "operating_pressure": "Normal operating pressure if shown (e.g., 40 barg)",
    "design_temperature": "Value with unit (e.g., 150C)",
    "operating_temperature": "Normal operating temperature if shown (e.g., 80C)",
    "material": "Material of construction (CS / SS316L / LTCS / Alloy 625)",
    "dimensions": {
    "diameter": "ID or OD with unit (e.g., 2000 mm ID)",
    "height": "T/T or T/L with unit (e.g., 6000 mm T/T)",
    "volume": "Capacity if shown (e.g., 18 m)",
    "other": "Any other dimensions shown"
    },
    "nozzles": [
    {"size": "8\" NPS", "rating": "300#", "service": "Inlet", "orientation": "Side / Top / Bottom"},
    {"size": "6\" NPS", "rating": "300#", "service": "Liquid Outlet", "orientation": "Bottom"}
    ],
    "code_compliance": "ASME VIII Div 1 / API 650 / etc.",
    "special_requirements": "NACE MR0175, PWHT, 100% RT, Internal coating, etc.",
    "notes": "Any equipment-specific notes from drawing",
    "missing_data": ["List any missing datasheet items"]
    }
    ],

    "instrument_schedule": [
    {
    "instrument_tag": "LT-3610-01A",
    "instrument_type": "Level Transmitter / Pressure Transmitter / Temperature Transmitter / Flow Transmitter / Switch / Indicator",
    "service": "Vessel level measurement / Process pressure / etc.",
    "location": "Field mounted / Control room / Local panel",
    "range": "Measurement range with units (e.g., 0-100% or 0-50 barg or -20 to 200C)",
    "alarm_setpoints": {
    "HH": "High-High alarm value (e.g., 90%)",
    "H": "High alarm value (e.g., 80%)",
    "L": "Low alarm value (e.g., 20%)",
    "LL": "Low-Low alarm value (e.g., 15%)"
    },
    "trip_setpoint": "Trip value if shown (e.g., LALL @ 10% trips pumps P-2001A/B)",
    "trip_action": "Describe shutdown/interlock action (e.g., Stop pumps P-2001A/B, Close FCV-101)",
    "fail_safe_position": "FC / FO / FL / FI (explain what happens on signal/power failure)",
    "safety_classification": "SIS SIL-2 / BPCS / Alarm only / DCS control",
    "signal_type": "4-20mA / HART / Digital / Pneumatic 3-15 psi",
    "power_supply": "24VDC / 120VAC / Instrument Air @ 20-30 psig",
    "connection_size": "1/2\" NPT / 3/4\" Flanged / etc.",
    "area_classification": "Zone 1 / Zone 2 / Safe Area / Class I Div 2",
    "notes": "Special notes (redundant, backup, local indication, etc.)",
    "missing_data": ["List any missing instrument data"]
    }
    ],

    "control_loops": [
    {
    "loop_tag": "FIC-101 (Flow loop identifier)",
    "loop_description": "Gas flow control to downstream unit",
    "sensor": "FE-101 (Orifice plate)",
    "transmitter": "FT-101 (Flow transmitter, 0-1000 m/h)",
    "controller": "FIC-101 (Flow indicator controller in DCS)",
    "final_element": "FCV-101 (6\" control valve, pneumatic actuator, FC)",
    "control_strategy": "Simple PID / Cascade / Ratio / Split-range / Override",
    "setpoint_source": "DCS operator / Cascade from TIC-102 / Ratio to FIC-102",
    "bypass_provision": "Manual bypass valve HV-101A/B around FCV-101",
    "notes": "Control philosophy notes"
    }
    ],

    "specification_breaks": [
    {
    "spec_break_id": "SB-1 / SPEC BREAK-1 / Sequential number",
    "location": "Line number and location (e.g., On line 6\"-P-1501 between V-101 and P-201A)",
    "upstream_spec": {
    "line_number": "6\"-P-1501-CS-150",
    "material_spec": "CS (Carbon Steel A106 Gr.B)",
    "pressure_class": "150#",
    "special_requirements": "None / NACE / PWHT / etc."
    },
    "downstream_spec": {
    "line_number": "6\"-P-1502-SS316-300",
    "material_spec": "SS316 (Stainless Steel 316)",
    "pressure_class": "300#",
    "special_requirements": "NACE MR0175"
    },
    "reason_for_break": "Material change (CS to SS316) due to corrosive service / Pressure class change / Temperature change / Special requirement",
    "break_properly_marked": "Yes / No (Is spec break symbol/notation clearly shown on drawing)",
    "transition_piece_required": "Yes / No - Reducer, Expander, Spool piece, Flange adapter",
    "transition_details": "6\" 150# CS to 6\" 300# SS316 flange adapter required",
    "spec_break_location_zone": "Zone on drawing where spec break occurs (for visualization)",
    "procurement_impact": "This break affects material procurement - CS pipe upstream, SS316 pipe downstream",
    "cost_impact": "High / Medium / Low (SS316 is ~3x cost of CS)",
    "installation_notes": "Ensure proper flange bolt-up torque per ASME B16.5 for dissimilar materials",
    "issues_found": ["Spec break not marked on drawing", "Missing transition piece specification", "Pressure class change not justified"]
    }
    ],

    "line_list": [
    {
    "line_number": "6\"-P1501-CS-150 (FULL line number as shown)",
    "line_size": "6 inch NPS",
    "service_code": "P = Process / U = Utility / F = Fuel Gas / CW = Cooling Water / IA = Instrument Air",
    "sequence_number": "1501 (area + sequence)",
    "material_spec": "CS = Carbon Steel A106 Gr.B / SS316 = Stainless Steel / LTCS = Low Temp Carbon Steel",
    "pressure_class": "150# / 300# / 600# ASME B16.5",
    "fluid_service": "HP Gas / LP Liquid / Steam / Cooling Water / etc.",
    "fluid_phase": "Liquid / Gas / Two-phase / Steam",
    "design_pressure": "Pressure if shown (e.g., 20 barg)",
    "design_temperature": "Temperature if shown (e.g., 150C)",
    "insulation": "None / Cold Insulation / Hot Insulation / Heat Tracing / Acoustic",
    "special_requirements": "Slope 1:100 to drain / High point vent / Low point drain / Steam traced",
    "from_equipment": "Starting point equipment tag (e.g., V-101 Nozzle N3)",
    "to_equipment": "Ending point equipment tag (e.g., P-201A/B Suction)",
    "flow_direction": "As indicated by arrows on drawing",
    "notes": "Special piping notes"
    }
    ],

    "safety_devices": [
    {
    "device_tag": "PSV-101",
    "device_type": "Pressure Safety Valve / Pressure Relief Valve / Rupture Disc / Thermal Relief Valve",
    "protected_equipment": "V-101 (Equipment tag protected by this device)",
    "set_pressure": "Set pressure with unit (e.g., 32 barg @ 150C per ASME VIII)",
    "design_pressure_of_protected_equipment": "Vessel MAWP (e.g., 35 barg @ 150C)",
    "relieving_capacity": "Flow capacity if shown (e.g., 50,000 kg/h)",
    "orifice_designation": "API 520 orifice (D / E / F / G / H / J / K / L / M / N / P / Q / R / T)",
    "inlet_size": "2\" NPT / 3\" 300# RF",
    "outlet_size": "3\" 150# RF",
    "discharge_destination": "Flare header F-1501 / Atmosphere / Safe location",
    "isolation_valves": {
    "inlet": "CSC-PSV-101A (Car-Sealed-Closed, only for maintenance)",
    "outlet": "CSO-PSV-101B (Car-Sealed-Open in discharge line)"
    },
    "safety_issues": "Flag if PSV is blocked, undersized, set pressure > MAWP, no discharge routing, etc.",
    "notes": "Special requirements (bellows for backpressure, balanced valve, pilot-operated, etc.)"
    },
    {
    "device_tag": "XV-ESD-101",
    "device_type": "Emergency Shutdown Valve (Motor-operated / Pneumatic)",
    "location": "Line 6\"-F-1501-CS-150 (fuel gas supply to heater)",
    "fail_position": "FC (Fail Close for safety - stop fuel flow on emergency)",
    "activation_logic": "ESD activated by: Manual ESD button, Fire detection FD-101, Gas detection GD-102",
    "response_time": "Full stroke time if noted (e.g., < 30 seconds)",
    "bypass_provision": "No bypass allowed (critical safety function)",
    "notes": "Part of SIS SIL-2 rated shutdown system"
    },
    {
    "device_tag": "BDV-101",
    "device_type": "Blowdown Valve (to Flare for depressuring)",
    "location": "V-101 blowdown connection",
    "discharge_to": "Flare header F-1501",
    "capacity": "Depressuring capacity if shown",
    "activation": "Manual / Automatic on ESD",
    "notes": "For emergency depressuring of vessel V-101"
    }
    ],

    "interlocks_trips": [
    {
    "interlock_id": "IL-01 (sequential number or identifier)",
    "description": "Low-Low level in V-101 trips discharge pumps",
    "trigger": "LALL-101 @ 15% (Low-Low level alarm switch)",
    "action": "Trip (stop) pumps P-2001A and P-2001B, Close valve XV-101",
    "reset_condition": "Manual reset after level > 25% and investigation",
    "safety_classification": "SIS SIL-1 / BPCS",
    "notes": "Prevents pump cavitation and dry running"
    }
    ],

    "utility_connections": [
    {
    "utility_type": "Cooling Water / Instrument Air / Nitrogen / Steam / Electrical",
    "connection_point": "Equipment tag where utility connects (e.g., E-101 Shell Side)",
    "supply_line": "Line number of supply (e.g., 4\"-CWS-U1501-CS)",
    "return_line": "Line number of return if applicable (e.g., 4\"-CWR-U1501-CS)",
    "design_flowrate": "Flow rate if shown (e.g., 50 m/h)",
    "design_pressure": "Utility pressure (e.g., CW @ 5 barg supply)",
    "isolation_valves": "List isolation valves for this utility",
    "control": "Control valve or flow control (e.g., TCV-101 controls CW flow)",
    "notes": "Special utility requirements"
    }
    ],

    "pfd_guidelines_compliance": {
    "legend_adherence": {
    "status": "Compliant / Non-compliant / Partially compliant",
    "details": "All symbols match drawing legend. Exception: Gate valve symbol on Line 4\"-P-1502 does not match legend.",
    "referenced_legend": "Legend on Sheet 1 of 3"
    },
    "isa_symbol_compliance": {
    "status": "Compliant / Non-compliant / Partially compliant",
    "standard_version": "ISA-5.1-2009",
    "details": "All instrument symbols comply with ISA-5.1. Transmitters shown with correct filled/empty circles based on mounting location.",
    "deviations": ["List any deviations from ISA standard"]
    },
    "equipment_numbering": {
    "convention": "Area-Type-Sequence (e.g., V-3610-01 = Vessel, Area 36, Unit 10, Seq.01)",
    "compliance": "Consistent / Inconsistent",
    "issues": ["Duplicate tag V-101 found", "Tag P-2001C skips sequence from P-2001B to P-2001D"]
    },
    "line_numbering": {
    "convention": "SIZE\"-SERVICE-SEQNO-SPEC-CLASS",
    "compliance": "Consistent / Inconsistent",
    "issues": ["Line 6\"-P-1501-CS missing pressure class", "Inconsistent material spec notation"]
    },
    "instrument_tagging": {
    "convention": "ISA-5.1 format: FirstLetter-ProcessVariable-LoopNumber-Suffix",
    "compliance": "Compliant / Non-compliant",
    "issues": ["FT-101 and FIC-102 appear to be same loop but different numbers"]
    },
    "drawing_notes": {
    "general_notes_complete": "Yes / No / Partially",
    "design_conditions_specified": "Yes / No",
    "abbreviations_explained": "Yes / No",
    "missing_notes": ["Design pressure/temperature not specified", "Material selection criteria not noted"]
    },
    "holds_and_notes_compliance": {
    "holds_section_present": "Yes / No",
    "holds_list": [
    {
    "hold_number": "HOLD-1 / H1 / etc.",
    "hold_description": "EXACT text of HOLD requirement from drawing (typically from right top corner table)",
    "compliance_status": "Compliant / Non-Compliant / Partially Compliant / Not Applicable",
    "verification_notes": "Detailed explanation of how drawing complies or violates this HOLD",
    "related_issues": ["List issue serial numbers if HOLD is violated"]
    }
    ],
    "general_notes_list": [
    {
    "note_number": "Note 1 / NOTE-01 / etc.",
    "note_text": "EXACT text of general note from drawing",
    "note_category": "Design Conditions / Material Selection / Safety / Operating Requirements / Testing / Other",
    "compliance_status": "Compliant / Non-Compliant / Partially Compliant",
    "verification_notes": "How drawing complies with this note",
    "related_issues": ["List issue serial numbers if note is violated"]
    }
    ],
    "design_notes_list": [
    {
    "note_description": "Design basis, pressure/temp ratings, fluid properties, special requirements",
    "compliance_status": "Compliant / Non-Compliant",
    "verification_notes": "Verification details"
    }
    ],
    "critical_violations": ["List any CRITICAL violations of HOLDS or NOTES that pose safety/operational risks"]
    },
    "referenced_documents": [
    "PFD-001 Rev.B (Process Flow Diagram)",
    "Piping Spec PS-101 Rev.3",
    "Instrument Spec IS-202 Rev.1",
    "Electrical Area Class Drawing EC-001",
    "ADNOC DEP 30.20.10.13-Gen (P&ID Standard)",
    "API 14C (Safety Systems)",
    "ISA-5.1-2009 (Instrumentation Symbols)"
    ],
    "completeness_check": {
    "title_block_complete": "Yes / No",
    "revision_block_complete": "Yes / No",
    "approval_signatures": "Complete / Incomplete / For approval",
    "sheet_numbering": "1 of 3 (all sheets accounted for)",
    "continuation_symbols": "All continuation symbols reference correct sheets"
    }
    },

    "summary": {
    "total_equipment": <count of equipment items>,
    "total_instruments": <count of instruments>,
    "total_control_loops": <count of control loops>,
    "total_lines": <count of piping lines>,
    "total_safety_devices": <count of PSVs, ESD valves, etc.>,
    "total_issues": <count of all issues>,
    "critical_count": <count of CRITICAL issues>,
    "major_count": <count of MAJOR issues>,
    "minor_count": <count of MINOR issues>,
    "observation_count": <count of OBSERVATION items>,
    "overall_compliance_score": <0-100 score based on issues found vs. total checks>,
    "drawing_complexity": "Simple / Moderate / Complex (based on equipment count, control loops, etc.)"
    },

    "issues": [
    {
    "serial_number": 1,
    "pid_reference": "EXACT tag/line/location from drawing (e.g., PSV-101, Line 6\"-P-1501, V-101 Nozzle N3)",
    "category": "Equipment Datasheet | Instrument Schedule | Control Loop | Line Sizing | Safety System | PFD Guidelines | Documentation | Utility System | Process Safety | Operability",
    "severity": "critical | major | minor | observation",
    "standard_reference": "Reference specific standard violated (e.g., ADNOC DEP 30.20.10.13 Clause 5.3.2, API 521, ISA-5.1, ASME VIII)",
    "issue_observed": "DETAILED description with EXACT values from drawing. Example: 'PSV-101 protecting vessel V-101 has set pressure of 35 barg, but vessel MAWP shown in datasheet is 32 barg. Set pressure exceeds MAWP by 3 barg (9.4% over), violating ASME VIII requirement that set pressure MAWP.'",
    "engineering_impact": "Explain WHY this is an issue: 'Vessel could be overpressured beyond design limits before PSV opens, risking vessel rupture. This is a critical safety violation.'",
    "action_required": "SPECIFIC corrective action with exact values: 'Change PSV-101 set pressure from 35 barg to 30 barg (90% of MAWP per API 521 recommendation for process relief). Verify PSV capacity is adequate at new set pressure. Update PSV datasheet and purchase specification.'",
    "location_on_drawing": {
    "zone": "Top-Left | Top-Center | Top-Right | Middle-Left | Middle-Center | Middle-Right | Bottom-Left | Bottom-Center | Bottom-Right",
    "proximity_description": "Describe location relative to major equipment (e.g., 'Adjacent to vessel V-101', 'On discharge line from pump P-2001A', 'Near title block', 'In equipment datasheet table', 'Center of process flow')",
    "visual_cues": "List visual landmarks to help locate (e.g., 'Red PSV symbol upstream of vessel', 'Equipment tag shown in bold box', 'In instrument schedule table row 5', 'Between cooler E-301 and separator V-302')",
    "drawing_section": "Process Equipment Area | Piping Section | Instrument Schedule Table | Equipment Datasheet Table | Title Block | Legend | Notes Section | Control Loop Diagram",
    "search_keywords": ["List of keywords to search on drawing: equipment tags, line numbers, instrument tags mentioned in this issue"]
    },
    "approval": "Pending / Approved / Ignored (default: Pending)",
    "remark": "Pending / Engineer's comment (default: Pending)",
    "status": "pending / approved / ignored (default: pending)",
    "related_issues": ["List serial numbers of related issues, e.g., [2, 5] if issues are interconnected"]
    }
    ]
    }
    ```

    SEVERITY CLASSIFICATION CRITERIA (ENGINEERING-BASED)

    **CRITICAL** - Immediate Safety Risk (Fix before operation):
    - Blocked PSV or PSV set pressure > MAWP (overpressure protection failure)
    - ESD valve with wrong fail-action (e.g., FO when should be FC for fuel isolation)
    - SIS instrument with wrong fail-safe position (defeats safety function)
    - Control valve critical fail-action wrong (e.g., FC for cooling water should be FO)
    - No overpressure protection on pressure vessel (ASME VIII violation)
    - Missing critical interlock (e.g., no low-level pump trip)
    - Instrument alarm hierarchy inverted (LL > HH, makes no sense)
    - Equipment pressure rating < operating pressure (will fail in service)
    - Wrong material for service (corrosion failure, e.g., CS for strong acid)
    - No fire/gas detection in hazardous area with no alternate protection

    **MAJOR** - Operational Failure Risk (Fix before startup):
    - Undersized piping (excessive velocity erosion, noise, vibration, pressure drop)
    - Oversized piping (poor drainage, slug flow, high cost)
    - Missing critical instrument (e.g., pump discharge pressure, vessel level)
    - Incomplete control loop (sensor present but no control valve, or vice versa)
    - Wrong line material specification (not suitable for service conditions)
    - Missing isolation valves (cannot maintain equipment without unit shutdown)
    - Check valve installed backwards (defeats purpose)
    - Missing utility connection (e.g., cooling water to exchanger)
    - Instrument range inadequate (does not cover operating envelope)
    - Control valve without bypass (cannot operate manually during valve maintenance)
    - Missing drain/vent valves at critical locations (operational difficulty)

    **MINOR** - Documentation/Clarity Issue (Fix during detail design):
    - Missing line number or incomplete line designation
    - Unclear equipment tag or duplicate numbering (documentation error)
    - Missing notes or incomplete title block information
    - Legend symbol does not match drawing usage
    - Inconsistent numbering convention (confusing but not safety risk)
    - Missing instrument range/alarm setpoint in schedule (data gap, not functional issue)
    - Typo in equipment tag or line number
    - Missing cross-reference to other drawings
    - Unlabeled valve or instrument (present but not tagged)
    - Abbreviation used without explanation in legend

    **OBSERVATION** - Improvement Suggestion (Consider for optimization):
    - Add redundant instrument for critical measurement (improve reliability)
    - Consider spare pump configuration (improve availability)
    - Add local pressure gauge for operator convenience (ease of operation)
    - Optimize piping routing (reduce pressure drop, improve layout)
    - Add sample point for process monitoring (operational improvement)
    - Consider adding drain valve for easier maintenance
    - Add strainer upstream of control valve (protect valve from debris)
    - Consider heat tracing for winterization (freeze protection)
    - Add flow indicator for operator awareness (not required but helpful)
    - Suggest adding bypass around equipment for maintenance flexibility

    EXAMPLE OF GOOD vs. BAD ANALYSIS

    ** BAD (Generic, Vague, Not Actionable):**
    {
    "serial_number": 1,
    "pid_reference": "Vessel",
    "severity": "major",
    "issue_observed": "Design pressure incorrect",
    "action_required": "Review and fix"
    }
    Problems: No specific tag, no exact values, no engineering explanation, vague action

    ** GOOD (Specific, Engineering-Grade, Actionable):**
    {
    "serial_number": 1,
    "pid_reference": "V-3610-01",
    "category": "Equipment Datasheet",
    "severity": "critical",
    "standard_reference": "ASME VIII Div.1 UG-125, API 521 Section 3.2",
    "issue_observed": "Equipment datasheet shows vessel V-3610-01 design pressure = 45 barg @ 150C with MAWP = 45 barg. However, PSV-101 protecting this vessel has set pressure = 48 barg (visible on PSV schedule table). Set pressure exceeds vessel MAWP by 3 barg (6.7% over). Per ASME VIII, relief device set pressure must not exceed MAWP of protected equipment.",
    "engineering_impact": "Vessel could be overpressured to 48 barg before PSV opens, exceeding design MAWP by 6.7%. This violates ASME pressure vessel code and risks vessel rupture under relief conditions. If vessel experiences overpressure event (blocked outlet, thermal expansion, runaway reaction), PSV will not open until vessel is already operating beyond safe design limits.",
    "action_required": "Reduce PSV-101 set pressure from 48 barg to maximum 45 barg (100% of MAWP). Recommend setting PSV-101 to 42 barg (93% of MAWP per API 521 good practice for process relief scenarios). Verify PSV relieving capacity remains adequate at reduced set pressure for worst-case relief scenario (fire case, blocked outlet case, thermal relief case). Update PSV specification sheet and procurement datasheet. If 48 barg set pressure is mandatory for process reasons, then increase vessel design pressure to minimum 50 barg and recalculate vessel wall thickness per ASME VIII.",
    "location_on_drawing": {
    "zone": "Top-Center",
    "proximity_description": "PSV-101 is located on top of vessel V-3610-01, connected to the vessel vapor outlet nozzle N2",
    "visual_cues": "Red PSV symbol with spring-loaded relief valve icon, tag PSV-101 shown adjacent to symbol, connected via short piping to vessel top nozzle",
    "drawing_section": "Process Equipment Area",
    "search_keywords": ["PSV-101", "V-3610-01", "set pressure", "MAWP", "equipment datasheet"]
    },
    "status": "pending",
    "related_issues": [5]
    }

    ---

    **CRITICAL INSTRUCTION FOR LOCATION INFORMATION:**
    For EVERY issue identified, you MUST provide detailed location information to help engineers quickly find the issue on the drawing:
    1. **Zone**: Divide the drawing mentally into a 3x3 grid (Top-Left, Top-Center, Top-Right, Middle-Left, Middle-Center, Middle-Right, Bottom-Left, Bottom-Center, Bottom-Right) and identify which zone contains the issue
    2. **Proximity Description**: Describe the location relative to major equipment, vessels, or prominent features visible on the drawing
    3. **Visual Cues**: Describe what the engineer should LOOK FOR - symbol type, color, size, surrounding elements
    4. **Drawing Section**: Identify if the issue is in the process flow area, a data table (equipment datasheet, instrument schedule, line list), title block, legend, or notes section
    5. **Search Keywords**: List exact tags, line numbers, or text that can be searched/found on the drawing

    This location information is MANDATORY for every issue - it significantly improves the usability of the analysis report.

    **CRITICAL INSTRUCTION FOR HOLDS & NOTES COMPLIANCE:**
    1. **Extract ALL HOLDS**: Look for HOLDS table (typically right top corner of drawing). Extract every HOLD item with exact text.
    2. **Extract ALL NOTES**: Extract general notes, design notes, and special requirements from notes section.
    3. **Verify Compliance**: For EVERY equipment item, instrument, line, and design decision:
    - Check if any HOLD applies to that item
    - Check if any NOTE constrains that design choice
    - If design violates a HOLD or NOTE, flag as CRITICAL issue with reference to specific HOLD/NOTE number
    4. **Cross-Reference Issues**: When creating issues list, reference which HOLD or NOTE is violated (if applicable)
    5. **Examples of HOLD/NOTE Violations to Check**:
    - HOLD: "All PSVs shall discharge to flare header" Check PSV discharge destinations
    - NOTE: "Design pressure for all equipment = 50 barg @ 150C" Verify all equipment datasheets match
    - NOTE: "All carbon steel piping in sour service requires NACE MR0175 compliance" Check material specs
    - HOLD: "Minimum 2oo3 voting for all SIS Level transmitters" Verify redundancy
    - NOTE: "All control valves fail-closed unless noted FC" Check fail-safe positions

    **CRITICAL INSTRUCTION FOR MINIMUM ISSUES REQUIREMENT:**
    **MANDATORY: You MUST identify AT LEAST {min_issues} SPECIFIC, ACTIONABLE ISSUES AND OBSERVATIONS.**

    This is NOT a goal to find fake issues - but a requirement to perform THOROUGH, COMPREHENSIVE analysis:
    1. **Check EVERY VISIBLE ELEMENT**: Equipment, instruments, lines, valves, notes, datasheets, schedules
    2. **Apply ALL VERIFICATION CATEGORIES**: Review all 7 verification sections systematically
    3. **Look for SUBTLE ISSUES**: Not just obvious errors - check compliance, optimization opportunities, documentation gaps
    4. **Include OBSERVATIONS**: Not just critical/major issues - include minor documentation improvements and operational optimization suggestions
    5. **Be SPECIFIC**: Each issue must reference exact tags, values, standards with detailed engineering explanation

    **Issue Mix Guidance** (Aim for balanced distribution):
    - Critical (Safety-related): 2-5 issues minimum
    - Major (Operational impact): 3-7 issues minimum
    - Minor (Documentation/clarity): 3-5 issues minimum
    - Observations (Improvements): 2-5 suggestions minimum

    **Common Areas to Find Issues** (Check ALL of these):
    Equipment datasheets (missing data, incorrect pressures/temperatures, material specs)
    Instrument schedules (missing ranges, alarm setpoints, fail-safe positions)
    Line lists (incomplete line numbers, sizing issues, spec breaks not marked)
    Safety devices (PSV set pressures, discharge routing, blocked vents)
    Control loops (missing bypass, wrong fail-action, no redundancy)
    Utility connections (cooling water, instrument air, steam, nitrogen)
    Isolation valves (maintenance access, locked open/closed, missing)
    Check valves (orientation, location, slam prevention)
    Drain and vent points (high point vents, low point drains, slope requirements)
    Electrical classifications (Zone markings, intrinsically safe barriers)
    Piping routing (dead legs, pocketing, thermal expansion, support locations)
    Material specifications (corrosion allowance, NACE compliance, temperature limits)
    Process safety (emergency shutdown logic, fire & gas detection, blowdown systems)
    Documentation (cross-references, legends, notes completeness, revision tracking)

    If you cannot find {min_issues} real issues, then you are NOT looking thoroughly enough. Review the drawing again systematically using the checklist above.

    **NOW ANALYZE THIS SPECIFIC P&ID DRAWING:**
Extract ALL visible data, perform engineering validation, identify REAL issues with EXACT values, and return comprehensive JSON output based on what you actually SEE in the drawing."""

    # Continue the PIDAnalysisService class definition
    def __init__(self):
        """Initialize OpenAI client with proper error handling"""
        # Try multiple sources for API key (soft-coded approach)
        api_key = None

        # Priority 1: Django settings
        if hasattr(settings, 'OPENAI_API_KEY'):
            api_key = settings.OPENAI_API_KEY
        print(f"[DEBUG] API key from Django settings: {api_key[:15] if api_key else 'None'}...")

        # Priority 2: Environment variable
        if not api_key or api_key == '':
        api_key = os.getenv('OPENAI_API_KEY')
        print(f"[DEBUG] API key from environment: {api_key[:15] if api_key else 'None'}...")

        # Validate API key
        if not api_key or api_key.strip() == '' or api_key == 'your-openai-api-key-here':
        error_msg = (
        " CRITICAL: OpenAI API key is NOT configured!\n"
        "This must be set in Railway environment variables.\n"
        "Instructions:\n"
        "1. Go to Railway Dashboard Your Project Variables\n"
        "2. Add: OPENAI_API_KEY = your-actual-key\n"
        "3. Get key from: https://platform.openai.com/api-keys\n"
        "Contact administrator immediately!"
        )
        print(f"[ERROR] {error_msg}")
        raise ValueError("OpenAI API key not configured - contact administrator")

        try:
        self.client = OpenAI(api_key=api_key)
        print(f"[INFO] OpenAI client initialized successfully (key: {api_key[:12]}...{api_key[-4:]})")
        except Exception as e:
        error_msg = f"Failed to initialize OpenAI client: {str(e)}"
        print(f"[ERROR] {error_msg}")
        raise ValueError(error_msg)

    def pdf_to_images(self, pdf_file, dpi: int = 300) -> List[str]:
        """
        Convert PDF pages to high-quality base64-encoded images

        Args:
        pdf_file: Either a file path (str) or a file-like object (Django FileField)
        dpi: DPI for image conversion (increased to 300 for better text recognition)

        Returns:
        List of base64-encoded PNG images
        """
        images_base64 = []

        # Handle both file paths and file objects (S3/Django FileField)
        if isinstance(pdf_file, str):
        # Local file path
        pdf_document = fitz.open(pdf_file)
        else:
        # File object (from S3 or Django FileField)
        # Read file content into memory
        pdf_file.seek(0) # Ensure we're at the start
        pdf_bytes = pdf_file.read()
        pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")

        try:
        # Convert each page to high-quality image
        for page_num in range(len(pdf_document)):
        page = pdf_document[page_num]

        # Render page to image with higher DPI for better text recognition
        zoom = dpi / 72 # 72 is default DPI
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False) # No alpha channel for smaller size

        # Convert to PIL Image for potential preprocessing
        img_data = pix.tobytes("png")

        # Encode to base64
        img_base64 = base64.b64encode(img_data).decode('utf-8')
        images_base64.append(img_base64)

        finally:
        pdf_document.close()

        return images_base64

    def _sanitize_json_keys(self, obj):
        """
        Recursively sanitize JSON object to remove whitespace/newlines from keys.
        Fixes AI responses with malformed keys like '\n "drawing_info"'.

        Soft-coded approach: Handles any level of nesting automatically.
        Enhanced to handle all edge cases including tabs, multiple quotes, etc.
        Ultra-defensive: Catches all exceptions during key sanitization.
        """
        try:
        if isinstance(obj, dict):
        # Clean dictionary keys and recursively sanitize values
        sanitized = {}
        for key, value in obj.items():
        try:
        # Aggressive cleaning: remove ALL whitespace characters, quotes, etc.
        clean_key = str(key).strip()
        # Remove leading/trailing: newlines, carriage returns, tabs, quotes
        for char in ['\n', '\r', '\t', '"', "'", ' ']:
        clean_key = clean_key.strip(char)
        # Additional pass to catch nested patterns like "\n \"key\""
        clean_key = clean_key.strip()

        if not clean_key: # Skip empty keys
        print(f"[WARNING] Skipping empty key after sanitization: {repr(key)}")
        continue

        sanitized[clean_key] = self._sanitize_json_keys(value)
        except Exception as key_error:
        print(f"[ERROR] Failed to sanitize key {repr(key)}: {str(key_error)}")
        # Try to add with original key as fallback
        try:
        sanitized[str(key)] = value
        except:
        print(f"[ERROR] Could not add key {repr(key)} even with fallback, skipping")
        continue
        return sanitized
        elif isinstance(obj, list):
        # Recursively sanitize list items
        return [self._sanitize_json_keys(item) for item in obj]
        else:
        # Return primitive values as-is
        return obj
        except Exception as e:
        print(f"[ERROR] Critical error in _sanitize_json_keys: {str(e)}")
        # If sanitization fails completely, return as-is (last resort)
        return obj

    def analyze_pid_drawing(self, pdf_file, drawing_number: Optional[str] = None) -> Dict[str, Any]:
        """
        Enhanced AI-Powered P&ID Analysis with intelligent retry logic and better error handling

        Args:
        pdf_file: Either a file path (str) or a Django FileField object
        drawing_number: Optional drawing number for RAG context retrieval

        Returns:
        Dictionary containing comprehensive analysis results with metadata
        """
        analysis_start_time = datetime.now()

        try:
        print(f"[AI ANALYSIS] Starting enhanced P&ID analysis for drawing: {drawing_number or 'unnamed'}")

        # Convert PDF pages to images with error handling
        images_base64 = self.pdf_to_images(pdf_file)

        if not images_base64:
        raise ValueError("Failed to convert PDF to images - file may be corrupted or invalid")

        print(f"[AI ANALYSIS] Successfully converted PDF to {len(images_base64)} page(s)")

        # Get RAG context if available
        rag_context = ""
        rag_metadata = {"used": False, "context_length": 0}
        rag_enabled = os.environ.get('RAG_ENABLED', 'false').lower() == 'true'

        if rag_enabled and drawing_number:
        try:
        print(f"[AI ANALYSIS] RAG enabled - retrieving context for drawing: {drawing_number}")
        rag_service = RAGService()
        rag_context = rag_service.retrieve_context(drawing_number)
        if rag_context:
        rag_metadata = {"used": True, "context_length": len(rag_context)}
        print(f"[AI ANALYSIS] Retrieved RAG context ({len(rag_context)} chars)")
        except Exception as rag_error:
        print(f"[AI ANALYSIS] RAG context retrieval failed: {str(rag_error)}")
        # Continue without RAG context - not critical for analysis

        # Build enhanced prompt with adaptive context
        analysis_prompt = self._build_analysis_prompt(rag_context)

        # Perform analysis with intelligent retry logic
        analysis_result = self._execute_analysis_with_retry(images_base64, analysis_prompt)

        # Calculate processing metrics
        processing_time = (datetime.now() - analysis_start_time).total_seconds()

        # Enhance result with metadata
        enhanced_result = self._enhance_analysis_result(
        analysis_result,
        processing_time,
        rag_metadata,
        len(images_base64)
        )

        print(f"[AI ANALYSIS] Analysis completed successfully in {processing_time:.2f}s")

        return enhanced_result

        except Exception as e:
        processing_time = (datetime.now() - analysis_start_time).total_seconds()
        error_msg = f"AI Analysis failed after {processing_time:.2f}s: {str(e)}"
        print(f"[AI ANALYSIS] ERROR: {error_msg}")

        # Return structured error response
        return {
        'success': False,
        'error': error_msg,
        'error_type': type(e).__name__,
        'processing_time': processing_time,
        'metadata': {
        'ai_model': 'GPT-4 Vision',
        'analysis_failed': True
        }
        }

        **Important:** Use the reference context above to enhance your analysis with specific standards, guidelines, and best practices. Cross-reference equipment specifications and design requirements with the provided documentation."""
        print(f"[INFO] Enhanced prompt with RAG context")

        print(f"[CONFIG] Analysis parameters: MIN_ISSUES={self.MIN_ISSUES_REQUIRED}, MAX_TOKENS={self.MAX_TOKENS}, TEMPERATURE={self.AI_TEMPERATURE}")

        # Build message content with all page images
        message_content = [
        {
        "type": "text",
        "text": analysis_prompt
        }
        ]

        # Add each page image
        for idx, img_base64 in enumerate(images_base64):
        message_content.append({
        "type": "image_url",
        "image_url": {
        "url": f"data:image/png;base64,{img_base64}",
        "detail": "high"
        }
        })

        # Call OpenAI API with vision capabilities
        print(f"[INFO] Calling OpenAI API (model: gpt-4o) with {len(images_base64)} page(s)...")
        print(f"[INFO] Request timestamp: {datetime.now().isoformat()}")

        try:
        response = self.client.chat.completions.create(
        model="gpt-4o",
        messages=[
        {
        "role": "system",
        "content": f"""You are a multidisciplinary Senior Engineering Team specializing in Oil & Gas P&ID verification with expert-level knowledge in:

        - Process Engineering (material & energy balance, process safety, HAZOP)
        - Piping Engineering (hydraulics, pipe stress, material selection, ASME B31.3)
        - Instrumentation & Control (control loops, SIS/SIL, ISA-5.1, alarm management)
        - Mechanical Engineering (pressure vessels ASME VIII, rotating equipment API 610/617/618)
        - Safety Engineering (API 520/521 relief systems, fire & gas detection, emergency shutdown)

        You MUST analyze each P&ID drawing as a UNIQUE engineering document based on its SPECIFIC content. Extract EXACT values, tags, and specifications visible in the drawing. Apply engineering judgment to identify REAL issues with practical safety and operational impact. Focus on compliance with ADNOC DEP, Shell DEP, API, ASME, ISA standards.

        Your analysis must be COMPREHENSIVE, SPECIFIC, and ACTIONABLE with engineering-grade detail. Never generate generic placeholder issues.

        CRITICAL REQUIREMENT: You MUST identify AT LEAST {self.MIN_ISSUES_REQUIRED} specific, actionable issues and observations. Perform thorough systematic verification of ALL drawing elements."""
        },
        {
        "role": "user",
        "content": message_content
        }
        ],
        max_tokens=self.MAX_TOKENS, # Soft-coded via environment variable (default: 16000)
        temperature=self.AI_TEMPERATURE, # Soft-coded via environment variable (default: 0.15)
        response_format={"type": "json_object"} # Force JSON response
        )
        print(f"[INFO] OpenAI API call successful")
        print(f"[INFO] Tokens used: {response.usage.total_tokens if hasattr(response, 'usage') else 'Unknown'}")
        except Exception as api_error:
        error_details = str(api_error)
        print(f"[ERROR] OpenAI API call failed: {error_details}")

        # Check for common API errors
        if "invalid_api_key" in error_details.lower():
        raise Exception("Invalid OpenAI API key. Please check your OPENAI_API_KEY in .env file")
        elif "insufficient_quota" in error_details.lower():
        raise Exception("OpenAI API quota exceeded. Please check your account billing")
        elif "rate_limit" in error_details.lower():
        raise Exception("OpenAI API rate limit reached. Please wait a moment and try again")
        else:
        raise Exception(f"OpenAI API error: {error_details}")

        # Extract and parse JSON response
        result_text = response.choices[0].message.content

        # Log raw response for debugging
        print(f"[DEBUG] OpenAI Raw Response length: {len(result_text) if result_text else 0} chars")
        print(f"[DEBUG] OpenAI Raw Response (first 500 chars): {result_text[:500] if result_text else 'EMPTY'}")

        if not result_text or not result_text.strip():
        raise ValueError(
        "OpenAI returned empty response. This may be due to:\n"
        "1. Invalid or expired API key\n"
        "2. Insufficient quota/credits\n"
        "3. PDF image quality issues\n"
        "Please check your OpenAI account and API key configuration."
        )

        # Clean potential markdown code blocks
        original_text = result_text
        if result_text.startswith("```json"):
        result_text = result_text.split("```json")[1].split("```")[0].strip()
        print(f"[DEBUG] Cleaned markdown ```json wrapper")
        elif result_text.startswith("```"):
        result_text = result_text.split("```")[1].split("```")[0].strip()
        print(f"[DEBUG] Cleaned markdown ``` wrapper")

        # Parse JSON response with comprehensive error handling
        try:
        analysis_result = json.loads(result_text)
        print(f"[INFO] Successfully parsed JSON response")

        # Debug: Log raw keys before sanitization
        if isinstance(analysis_result, dict):
        raw_keys = list(analysis_result.keys())
        print(f"[DEBUG] Raw JSON keys before sanitization: {raw_keys}")
        print(f"[DEBUG] Raw keys (repr): {repr(raw_keys)}")

        # CRITICAL FIX: Sanitize JSON keys (remove whitespace, newlines from keys)
        # AI sometimes adds formatting that breaks key access
        analysis_result = self._sanitize_json_keys(analysis_result)
        print(f"[INFO] JSON keys sanitized")

        # Debug: Log clean keys after sanitization
        if isinstance(analysis_result, dict):
        clean_keys = list(analysis_result.keys())
        print(f"[DEBUG] Clean JSON keys after sanitization: {clean_keys}")

        print(f"[INFO] Issues found: {len(analysis_result.get('issues', []))}")

        except json.JSONDecodeError as json_error:
        print(f"[ERROR] JSON Decode Error: {str(json_error)}")
        print(f"[ERROR] Error at position: {json_error.pos}")
        print(f"[ERROR] Problematic text (first 1000 chars): {result_text[:1000]}")
        print(f"[ERROR] Original response (first 1000 chars): {original_text[:1000]}")

        # Soft-coded error messages based on error type
        error_context = os.getenv('PID_ERROR_CONTEXT', 'detailed') # detailed | simple

        if error_context == 'simple':
        raise ValueError(f"Invalid JSON response from AI: {str(json_error)}")
        else:
        # Detailed error message
        raise ValueError(
        f"OpenAI returned invalid JSON format.\n"
        f"Error: {str(json_error)}\n"
        f"Position: {json_error.pos}\n"
        f"This usually means:\n"
        f"1. API returned an error message instead of analysis\n"
        f"2. API key is invalid or has insufficient permissions\n"
        f"3. Response was truncated due to token limits\n"
        f"4. Drawing image quality too poor for analysis\n"
        f"Response preview: {original_text[:200]}...\n"
        f"Check Railway logs for full response"
        )

        # Validate and enrich response with soft-coded defaults (defensive approach)
        try:
        min_issues_found = len(analysis_result.get('issues', []))
        except Exception as e:
        print(f"[ERROR] Failed to count issues: {str(e)}")
        min_issues_found = 0

        # Safely check and add missing keys with try-except for each operation
        try:
        if 'issues' not in analysis_result:
        analysis_result['issues'] = []
        print(f"[WARNING] No 'issues' key in response, initialized empty array")
        except Exception as e:
        print(f"[ERROR] Failed to check/add 'issues' key: {str(e)}")
        # Try to create a new dict with the key
        try:
        analysis_result = {'issues': [], **analysis_result}
        except:
        print(f"[ERROR] Could not add 'issues' key, continuing...")

        try:
        if 'summary' not in analysis_result:
        # Calculate summary from issues
        issues_list = analysis_result.get('issues', [])
        critical = sum(1 for i in issues_list if i.get('severity', '').lower() == 'critical')
        major = sum(1 for i in issues_list if i.get('severity', '').lower() == 'major')
        minor = sum(1 for i in issues_list if i.get('severity', '').lower() == 'minor')
        observation = sum(1 for i in issues_list if i.get('severity', '').lower() == 'observation')

        analysis_result['summary'] = {
        'total_issues': len(issues_list),
        'critical_count': critical,
        'major_count': major,
        'minor_count': minor,
        'observation_count': observation
        }
        print(f"[INFO] Generated summary: {analysis_result['summary']}")
        except Exception as e:
        print(f"[ERROR] Failed to check/generate 'summary': {str(e)}")
        # Add minimal summary
        try:
        analysis_result['summary'] = {
        'total_issues': 0,
        'critical_count': 0,
        'major_count': 0,
        'minor_count': 0,
        'observation_count': 0
        }
        except:
        print(f"[ERROR] Could not add 'summary' key, continuing...")

        try:
        if 'drawing_info' not in analysis_result:
        analysis_result['drawing_info'] = {
        'drawing_number': drawing_number or 'Unknown',
        'drawing_title': 'P&ID Drawing',
        'revision': 'Unknown',
        'analysis_date': datetime.now().isoformat()
        }
        print(f"[WARNING] No 'drawing_info' in response, using defaults")
        except Exception as e:
        print(f"[ERROR] Failed to check/add 'drawing_info': {str(e)}")
        # Add minimal drawing_info
        try:
        analysis_result['drawing_info'] = {
        'drawing_number': drawing_number or 'Unknown',
        'drawing_title': 'P&ID Drawing',
        'revision': 'Unknown',
        'analysis_date': datetime.now().isoformat()
        }
        except:
        print(f"[ERROR] Could not add 'drawing_info' key, continuing...")

        # Validation: Check minimum issues requirement (soft-coded with flexibility)
        if self.STRICT_MIN_ISSUES:
        # STRICT mode: Enforce minimum issues requirement
        if min_issues_found < self.MIN_ISSUES_REQUIRED:
        print(f"[WARNING] Only {min_issues_found} issues found, expected minimum {self.MIN_ISSUES_REQUIRED}")
        print(f"[WARNING] AI may need re-analysis with different parameters")
        print(f"[INFO] STRICT mode enabled - minimum issues required")
        else:
        print(f"[SUCCESS] Found {min_issues_found} issues (minimum {self.MIN_ISSUES_REQUIRED} satisfied)")
        else:
        # FLEXIBLE mode: Accept any number of issues
        if min_issues_found < self.MIN_ISSUES_REQUIRED:
        print(f"[INFO] Found {min_issues_found} issues (target was {self.MIN_ISSUES_REQUIRED})")
        print(f"[INFO] FLEXIBLE mode - accepting dynamic issue count")
        else:
        print(f"[SUCCESS] Found {min_issues_found} issues (exceeded minimum {self.MIN_ISSUES_REQUIRED})")

        return analysis_result

        except KeyError as ke:
        # KeyError when accessing malformed JSON keys
        print(f"[ERROR] KeyError accessing JSON key: {str(ke)}")
        print(f"[ERROR] This should not happen after sanitization!")
        print(f"[RECOVERY] Entering emergency recovery mode...")

        # Emergency recovery: Build minimal valid response
        try:
        # Try to extract whatever we can from analysis_result
        recovered_issues = []
        recovered_summary = {
        'total_issues': 0,
        'critical_count': 0,
        'major_count': 0,
        'minor_count': 0,
        'observation_count': 0
        }
        recovered_drawing_info = {
        'drawing_number': drawing_number or 'Unknown',
        'drawing_title': 'P&ID Drawing',
        'revision': 'Unknown',
        'analysis_date': datetime.now().isoformat()
        }

        # Defensively try to access analysis_result
        if 'analysis_result' in locals():
        print(f"[RECOVERY] Attempting to extract data from malformed response...")

        # Try to get issues using ultra-safe approach
        try:
        if isinstance(analysis_result, dict):
        # Re-sanitize first
        sanitized = self._sanitize_json_keys(analysis_result)
        print(f"[RECOVERY] Re-sanitized successfully")

        # Extract issues
        if 'issues' in sanitized:
        recovered_issues = sanitized['issues']
        print(f"[RECOVERY] Extracted {len(recovered_issues)} issues")

        # Extract summary
        if 'summary' in sanitized:
        recovered_summary = sanitized['summary']
        print(f"[RECOVERY] Extracted summary")

        # Extract drawing_info
        if 'drawing_info' in sanitized:
        recovered_drawing_info = sanitized['drawing_info']
        print(f"[RECOVERY] Extracted drawing_info")

        except Exception as extract_error:
        print(f"[RECOVERY] Extraction failed: {str(extract_error)}")
        print(f"[RECOVERY] Using minimal defaults")

        # Return minimal valid structure
        print(f"[RECOVERY] Returning minimal valid structure with {len(recovered_issues)} issues")
        return {
        'drawing_info': recovered_drawing_info,
        'issues': recovered_issues,
        'summary': recovered_summary
        }

        except Exception as recovery_error:
        print(f"[ERROR] Emergency recovery failed: {str(recovery_error)}")
        # Last resort: return absolute minimum structure
        print(f"[RECOVERY] Returning absolute minimum structure")
        return {
        'drawing_info': {
        'drawing_number': drawing_number or 'Unknown',
        'drawing_title': 'P&ID Drawing (Recovery Mode)',
        'revision': 'Unknown',
        'analysis_date': datetime.now().isoformat(),
        'error': f'Partial recovery from malformed response: {str(ke)}'
        },
        'issues': [],
        'summary': {
        'total_issues': 0,
        'critical_count': 0,
        'major_count': 0,
        'minor_count': 0,
        'observation_count': 0
        }
        }
        except ValueError as ve:
        # ValueError from JSON parsing or validation
        print(f"[ERROR] Value error in analysis: {str(ve)}")
        raise Exception(f"Analysis failed: {str(ve)}")
        except Exception as e:
        print(f"[ERROR] P&ID analysis exception: {type(e).__name__}: {str(e)}")
        import traceback
        traceback.print_exc()

        # Soft-coded error message format
        error_format = os.getenv('PID_ERROR_FORMAT', 'technical') # technical | user-friendly

        if error_format == 'user-friendly':
        raise Exception(f"Analysis failed: {str(e)}")
        else:
        raise Exception(f"Analysis failed: {type(e).__name__}: {str(e)}")

    def generate_report_summary(self, issues: List[Dict]) -> Dict[str, int]:
        """Generate summary statistics from issues"""
        summary = {
        'total_issues': len(issues),
        'approved_count': 0,
        'ignored_count': 0,
        'pending_count': 0,
        'critical_count': 0,
        'major_count': 0,
        'minor_count': 0,
        'observation_count': 0
        }

        for issue in issues:
        # Count by status
        status = issue.get('status', 'pending').lower()
        if status == 'approved':
        summary['approved_count'] += 1
        elif status == 'ignored':
        summary['ignored_count'] += 1
        else:
        summary['pending_count'] += 1

        # Count by severity
        severity = issue.get('severity', 'observation').lower()
        if severity == 'critical':
        summary['critical_count'] += 1
        elif severity == 'major':
        summary['major_count'] += 1
        elif severity == 'minor':
        summary['minor_count'] += 1
        else:
        summary['observation_count'] += 1

        return summary

    def _build_analysis_prompt(self, rag_context: str = "") -> str:
        """Build intelligent analysis prompt with optional RAG context"""
        base_prompt = self.ANALYSIS_PROMPT.format(min_issues=self.MIN_ISSUES_REQUIRED)

        if rag_context:
        return f"""**REFERENCE CONTEXT FROM ENGINEERING STANDARDS:**

        {rag_context}

        ---

        {base_prompt}

        **CONTEXT-AWARE ANALYSIS INSTRUCTIONS:**
        - Use the provided reference context to enhance your analysis accuracy
        - Cross-reference findings against the standards mentioned above
        - Highlight any deviations from the referenced guidelines
        """

        return base_prompt

    def _execute_analysis_with_retry(self, images_base64: List[str], prompt: str) -> Dict[str, Any]:
        """Execute analysis with intelligent retry logic"""
        last_exception = None

        for attempt in range(self.RETRY_ATTEMPTS):
        try:
        print(f"[AI ANALYSIS] Analysis attempt {attempt + 1}/{self.RETRY_ATTEMPTS}")

        # Prepare image content for API
        image_content = []
        for i, image_b64 in enumerate(images_base64):
        image_content.append({
        "type": "image_url",
        "image_url": {
        "url": f"data:image/png;base64,{image_b64}",
        "detail": "high"
        }
        })

        # Call OpenAI API with adaptive temperature
        temperature = self.AI_TEMPERATURE + (attempt * 0.05) # Slightly increase creativity on retries

        response = self.client.chat.completions.create(
        model="gpt-4o",
        messages=[
        {
        "role": "system",
        "content": "You are an advanced AI engineering consultant specializing in P&ID analysis."
        },
        {
        "role": "user",
        "content": [
        {"type": "text", "text": prompt},
        *image_content
        ]
        }
        ],
        max_tokens=self.MAX_TOKENS,
        temperature=temperature,
        response_format={"type": "json_object"}
        )

        # Extract and validate response
        result_text = response.choices[0].message.content
        tokens_used = response.usage.total_tokens

        print(f"[AI ANALYSIS] API call successful (tokens: {tokens_used})")

        # Parse JSON response with error handling
        try:
        result_json = self._extract_json_from_response(result_text)
        result_json['tokens_used'] = tokens_used
        result_json['ai_temperature'] = temperature
        result_json['attempt_number'] = attempt + 1

        return result_json

        except json.JSONDecodeError as json_error:
        print(f"[AI ANALYSIS] WARNING: JSON parsing failed on attempt {attempt + 1}: {str(json_error)}")
        if attempt == self.RETRY_ATTEMPTS - 1:
        # Last attempt - return fallback structure
        return self._create_fallback_response(result_text, tokens_used)
        continue

        except Exception as e:
        last_exception = e
        print(f"[AI ANALYSIS] ERROR: Attempt {attempt + 1} failed: {str(e)}")

        if attempt < self.RETRY_ATTEMPTS - 1:
        import time
        wait_time = (attempt + 1) * 2 # Exponential backoff
        print(f"[AI ANALYSIS] Retrying in {wait_time}s...")
        time.sleep(wait_time)

        # All attempts failed
        raise last_exception or Exception("Analysis failed after all retry attempts")

    def _enhance_analysis_result(self, result: Dict[str, Any], processing_time: float,
        rag_metadata: Dict, page_count: int) -> Dict[str, Any]:
        """Enhance analysis result with metadata and quality metrics"""

        # Calculate confidence score based on various factors
        confidence_score = self._calculate_confidence_score(result)

        # Add comprehensive metadata
        enhanced_result = {
        **result,
        'success': True,
        'metadata': {
        'ai_model': 'GPT-4 Vision',
        'processing_time': f"{processing_time:.2f}s",
        'confidence_score': confidence_score,
        'analysis_type': self.ANALYSIS_DEPTH,
        'page_count': page_count,
        'rag_context_used': rag_metadata['used'],
        'rag_context_length': rag_metadata['context_length'],
        'analysis_timestamp': datetime.now().isoformat(),
        'tokens_used': result.get('tokens_used', 'N/A'),
        'ai_temperature': result.get('ai_temperature', self.AI_TEMPERATURE),
        'retry_attempt': result.get('attempt_number', 1)
        }
        }

        return enhanced_result

    def _calculate_confidence_score(self, result: Dict[str, Any]) -> str:
        """Calculate confidence score based on analysis quality indicators"""
        score = 0.7 # Base confidence

        # Check for detailed issues
        issues = result.get('issues', [])
        if len(issues) >= 5:
        score += 0.1
        if len(issues) >= 10:
        score += 0.1

        # Check for detailed descriptions
        detailed_issues = [issue for issue in issues if len(issue.get('issue_observed', '')) > 50]
        if len(detailed_issues) > len(issues) * 0.7:
            score += 0.1

        # Convert to descriptive confidence
        if score >= 0.9:
            return "Very High"
        elif score >= 0.8:
            return "High"
        elif score >= 0.7:
            return "Good"
        else:
            return "Moderate"

    def _create_fallback_response(self, response_text: str, tokens_used: int) -> Dict[str, Any]:
        """Create fallback response when JSON parsing fails"""
        return {
            'issues': [{
                'serial_number': 1,
                'pid_reference': 'SYSTEM-001',
                'issue_observed': 'AI analysis completed but response format requires manual review',
                'action_required': 'Review raw analysis output for detailed findings',
                'severity': 'observation',
                'category': 'analysis_format',
                'status': 'pending'
            }],
            'total_issues': 1,
            'raw_response': response_text,
            'tokens_used': tokens_used,
            'parsing_error': True
        }
