"""Versioned horizontal separator engineering aid; no persistence or AI calls."""
import math

VERSION = "separator-1.0"
INPUTS = {
    "pressure": ("Operating pressure", "barg"),
    "temperature": ("Operating temperature", "C"),
    "gas_flow": ("Standard gas flow", "MMSCFD"),
    "gas_mw": ("Gas molecular weight", "kg/kmol"),
    "gas_z": ("Operating gas Z", "-"),
    "gas_viscosity": ("Gas viscosity (information only)", "cP"),
    "oil_flow": ("Actual oil flow", "m3/h"),
    "oil_density": ("Oil density", "kg/m3"),
    "oil_viscosity": ("Oil viscosity", "cP"),
    "water_flow": ("Actual water flow", "m3/h"),
    "water_density": ("Water density", "kg/m3"),
    "water_viscosity": ("Water viscosity", "cP"),
}
CRITERIA = {
    "gas_margin": ("Gas flow margin", "fraction", .1),
    "oil_margin": ("Oil flow margin", "fraction", .1),
    "water_margin": ("Water flow margin", "fraction", .1),
    "oil_retention": ("Oil retention", "min", 5),
    "water_retention": ("Water retention", "min", 5),
    "water_drop": ("Water drop in oil", "micrometre", 500),
    "oil_drop": ("Oil drop in water", "micrometre", 200),
    "k": ("Souders-Brown K, mesh pad", "m/s", .12),
    "ld": ("Target L/D", "-", 4),
    "effective": ("Effective / T-T length", "fraction", .8),
    "min_id": ("Minimum ID", "m", .6),
    "id_step": ("ID rounding increment", "m", .1),
    "length_step": ("Length rounding increment", "m", .1),
    "hhll": ("HHLL / ID", "fraction", .7),
    "hll": ("HLL / ID", "fraction", .65),
    "nll": ("NLL / ID", "fraction", .6),
    "lll": ("LLL / ID", "fraction", .55),
    "llll": ("LLLL / ID", "fraction", .5),
    "interface_alarm": ("Interface alarm offset", "mm", 100),
    "interface_trip": ("Interface trip offset", "mm", 200),
    "inlet_momentum": ("Inlet maximum density x velocity squared", "Pa", 6000),
    "gas_momentum": ("Gas outlet maximum density x velocity squared", "Pa", 4500),
    "oil_velocity": ("Oil outlet velocity limit", "m/s", 1),
    "water_velocity": ("Water outlet velocity limit", "m/s", 1),
    "atmosphere": ("Atmospheric pressure", "bar(a)", 1.01325),
    "standard_pressure": ("Gas standard pressure", "kPa(a)", 101.325),
    "standard_temperature": ("Gas standard temperature", "K", 288.71),
    "standard_z": ("Gas standard Z", "-", 1),
    "gravity": ("Gravity", "m/s2", 9.81),
    "gas_constant": ("Universal gas constant", "kJ/kmol/K", 8.314),
    "scf_m3": ("Workbook scf conversion", "m3/scf", .0283168),
    "pressure_margin": ("Preliminary design pressure margin", "fraction", .1),
    "pressure_offset": ("Minimum design pressure margin", "bar", 1),
    "temperature_offset": ("Preliminary design temperature margin", "C", 25),
    "mdmt": ("Preliminary minimum metal temperature", "C", 0),
    "stress": ("Preliminary allowable stress", "MPa", 138),
    "efficiency": ("Preliminary joint efficiency", "-", 1),
    "corrosion": ("Corrosion allowance", "mm", 3),
    "min_thickness": ("Minimum thickness including corrosion", "mm", 6),
    "steel_density": ("Steel density", "kg/m3", 7850),
    "weight_margin": ("Internals/supports weight allowance", "fraction", .2),
    "hydro_factor": ("Preliminary hydrotest factor, stress ratio 1", "-", 1.3),
}
PIPES = [(1, 26.6), (1.5, 40.9), (2, 52.5), (3, 77.9), (4, 102.3),
         (6, 154.1), (8, 202.7), (10, 254.5), (12, 304.8), (14, 336.6),
         (16, 387.4), (18, 438.2), (20, 489), (24, 590.6), (30, 742.9), (36, 893.8)]


def number(value, path):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{path}: provide a finite numeric value.")
    return float(value)


def segment(height):
    return (math.acos(1 - 2 * height) - (1 - 2 * height) * math.sqrt(1 - (1 - 2 * height) ** 2)) / math.pi


def interface_height(fraction):
    if not 0 <= fraction <= 1:
        raise ValueError("Water area fraction is outside the vessel cross-section.")
    low, high = 0.0, 1.0
    for _ in range(60):
        midpoint = (low + high) / 2
        if segment(midpoint) < fraction:
            low = midpoint
        else:
            high = midpoint
    height = (low + high) / 2
    if abs(segment(height) - fraction) > 1e-12:
        raise ValueError("Interface solver did not converge.")
    return height


def rounded(value, increment):
    return math.ceil(value / increment - 1e-12) * increment


def calculate(payload):
    if not isinstance(payload, dict) or (payload.get("basis_confirmed") is not True and payload.get("execution_mode") != "automatic_preview"):
        raise ValueError("Confirm the engineering criteria, gas standard basis and phase identities.")
    raw_criteria = payload.get("criteria")
    if not isinstance(raw_criteria, dict) or set(raw_criteria) != set(CRITERIA):
        raise ValueError("Provide all criteria from the calculator schema; unknown criteria are not supported.")
    c = {key: number(raw_criteria[key], f"criteria.{key}") for key in CRITERIA}
    nonnegative = {"gas_margin", "oil_margin", "water_margin", "pressure_margin",
                   "temperature_offset", "corrosion", "weight_margin"}
    for key, val in c.items():
        if key == "mdmt":
            continue
        if (key in nonnegative and val < 0) or (key not in nonnegative and val <= 0):
            raise ValueError(f"criteria.{key}: value is outside the permitted positive/nonnegative range.")
    if not 0 < c["llll"] < c["lll"] < c["nll"] < c["hll"] < c["hhll"] < 1:
        raise ValueError("Liquid levels must satisfy 0 < LLLL < LLL < NLL < HLL < HHLL < 1.")
    if c["effective"] > 1 or c["efficiency"] > 1 or c["interface_alarm"] >= c["interface_trip"]:
        raise ValueError("Effective length and joint efficiency must be <= 1; interface alarm offset must be below trip offset.")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= 100:
        raise ValueError("Select 1 to 100 operating cases.")
    cases = []
    ids = set()
    for index, raw in enumerate(raw_cases):
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str) or not raw["id"] or raw["id"] in ids:
            raise ValueError(f"cases.{index}: provide a unique case ID.")
        ids.add(raw["id"])
        if not isinstance(raw.get("name"), str) or not raw["name"].strip():
            raise ValueError(f"cases.{index}: provide a case name.")
        inputs = raw.get("inputs")
        if not isinstance(inputs, dict) or set(inputs) != set(INPUTS):
            raise ValueError(f"cases.{index}: provide all supported input fields.")
        x = {}
        for key in INPUTS:
            val = inputs[key]
            x[key] = None if val is None else number(val, f"cases.{index}.{key}")
        for key in ("pressure", "temperature", "gas_flow", "oil_flow", "water_flow"):
            if x[key] is None:
                raise ValueError(f"{raw['name']}.{key}: required input is missing.")
        if min(x["gas_flow"], x["oil_flow"], x["water_flow"]) < 0 or x["gas_flow"] + x["oil_flow"] + x["water_flow"] == 0:
            raise ValueError(f"{raw['name']}: phase flows must be nonnegative and at least one phase must flow.")
        if x["pressure"] + c["atmosphere"] <= 0 or x["temperature"] <= -273.15:
            raise ValueError(f"{raw['name']}: absolute pressure and temperature must be positive.")
        for phase, keys in [
            ("gas", ("gas_mw", "gas_z")),
            ("oil", ("oil_density", "oil_viscosity")),
            ("water", ("water_density", "water_viscosity")),
        ]:
            if x[f"{phase}_flow"] > 0 and any(x[key] is None or x[key] <= 0 for key in keys):
                raise ValueError(f"{raw['name']}: positive {phase} properties are required for a flowing phase.")
        if any(x[key] is not None and x[key] <= 0 for key in INPUTS if key not in {"pressure", "temperature", "gas_flow", "oil_flow", "water_flow"}):
            raise ValueError(f"{raw['name']}: provided fluid properties must be positive.")
        if x["gas_flow"] > 0 and x["oil_flow"] + x["water_flow"] == 0:
            raise ValueError(f"{raw['name']}: gas-only service is outside this separator calculator's scope.")
        qo = x["oil_flow"] * (1 + c["oil_margin"])
        qw = x["water_flow"] * (1 + c["water_margin"])
        qstd = x["gas_flow"] * (1 + c["gas_margin"])
        pressure = (x["pressure"] + c["atmosphere"]) * 100
        temperature = x["temperature"] + 273.15
        rg = pressure * x["gas_mw"] / (x["gas_z"] * c["gas_constant"] * temperature) if qstd else None
        qg = qstd * 1e6 * c["scf_m3"] / 86400 * c["standard_pressure"] / pressure * temperature / c["standard_temperature"] * x["gas_z"] / c["standard_z"] if qstd else 0
        liquid_density = x["oil_density"] if qo else x["water_density"]
        if rg is not None and liquid_density <= rg:
            raise ValueError(f"{raw['name']}: liquid density must exceed gas density.")
        if qo and qw and x["water_density"] <= x["oil_density"]:
            raise ValueError(f"{raw['name']}: water density must exceed oil density for the supported phase arrangement.")
        vmax = c["k"] * math.sqrt((liquid_density - rg) / rg) if qg else None
        gas_id = math.sqrt(4 * qg / vmax / (1 - segment(c["hhll"])) / math.pi) if qg else 0
        volume = (qo * c["oil_retention"] + qw * c["water_retention"]) / 60
        liquid_id = (4 * volume / (math.pi * segment(c["nll"]) * c["effective"] * c["ld"])) ** (1 / 3)
        required = max(gas_id, liquid_id, c["min_id"])
        control = "Minimum ID" if c["min_id"] >= max(gas_id, liquid_id) else "Liquid retention" if liquid_id >= gas_id else "Gas capacity"
        cases.append({"id": raw["id"], "name": raw["name"], "inputs": x,
                      "gas_design": qstd, "oil_design": qo, "water_design": qw,
                      "gas_density": rg, "gas_actual": qg, "gas_allowable": vmax,
                      "gas_required_id": gas_id, "liquid_required_id": liquid_id,
                      "required_id": required, "control": control})
    governing = max(cases, key=lambda case: case["required_id"])
    diameter = rounded(governing["required_id"], c["id_step"])
    length = rounded(diameter * c["ld"], c["length_step"])
    effective = length * c["effective"]
    area = math.pi * diameter ** 2 / 4
    nozzle_requirements = {key: [] for key in ("inlet", "gas", "oil", "water")}
    for case in cases:
        x = case["inputs"]
        qo, qw, qg = case["oil_design"], case["water_design"], case["gas_actual"]
        warnings = []
        water_area = qw / 60 * c["water_retention"] / effective
        water_fraction = water_area / area
        if water_fraction > segment(c["nll"]) + 1e-12:
            raise ValueError(f"{case['name']}: required water area exceeds available liquid area.")
        nil = interface_height(min(water_fraction, segment(c["nll"]))) * diameter * 1000
        oil_area = max(0, segment(c["nll"]) * area - water_area)
        oil_time = oil_area * effective / (qo / 60) if qo else None
        water_time = water_area * effective / (qw / 60) if qw else None
        utilization = qg / (area * (1 - segment(c["hhll"]))) / case["gas_allowable"] if qg else None
        if utilization is not None and utilization > 1 + 1e-10:
            warnings.append("Gas capacity fails.")
        if oil_time is not None and oil_time + 1e-10 < c["oil_retention"]:
            warnings.append("Oil retention fails.")
        settling = {"water_re": None, "oil_re": None, "water_time": None, "oil_time": None}
        if qo and qw:
            delta = x["water_density"] - x["oil_density"]
            vw = c["gravity"] * delta * (c["water_drop"] * 1e-6) ** 2 / (18 * x["oil_viscosity"] / 1000)
            vo = c["gravity"] * delta * (c["oil_drop"] * 1e-6) ** 2 / (18 * x["water_viscosity"] / 1000)
            settling = {
                "water_re": x["oil_density"] * vw * c["water_drop"] * 1e-6 / (x["oil_viscosity"] / 1000),
                "oil_re": x["water_density"] * vo * c["oil_drop"] * 1e-6 / (x["water_viscosity"] / 1000),
                "water_time": (c["nll"] * diameter - nil / 1000) / vw / 60,
                "oil_time": nil / 1000 / vo / 60,
            }
            if settling["water_re"] >= 2 or settling["oil_re"] >= 2:
                warnings.append("Settling outside stated Stokes range (Re >= 2); separation is not validated.")
            if settling["water_time"] > oil_time or settling["oil_time"] > water_time:
                warnings.append("Droplet settling/rise time exceeds phase retention.")
            if not 0 < nil - c["interface_trip"] < nil - c["interface_alarm"] < nil < nil + c["interface_alarm"] < nil + c["interface_trip"] < c["llll"] * diameter * 1000:
                warnings.append("Interface alarm/trip levels do not fit below LLLL and above vessel bottom.")
        else:
            warnings.append("Single liquid phase: interface and liquid-liquid settling are not applicable.")
        mixture_flow = qg + (qo + qw) / 3600
        mixture_density = (qg * (case["gas_density"] or 0) + qo / 3600 * (x["oil_density"] or 0) + qw / 3600 * (x["water_density"] or 0)) / mixture_flow
        required_nozzles = {
            "inlet": math.sqrt(4 * mixture_flow / (math.pi * math.sqrt(c["inlet_momentum"] / mixture_density))) * 1000,
            "gas": math.sqrt(4 * qg / (math.pi * math.sqrt(c["gas_momentum"] / case["gas_density"]))) * 1000 if qg else 0,
            "oil": math.sqrt(4 * qo / 3600 / (math.pi * c["oil_velocity"])) * 1000,
            "water": math.sqrt(4 * qw / 3600 / (math.pi * c["water_velocity"])) * 1000,
        }
        for key, val in required_nozzles.items():
            nozzle_requirements[key].append((val, case["id"]))
        case.update({"nil_mm": nil if qo and qw else None, "oil_retention": oil_time,
                     "water_retention": water_time, "gas_utilization": utilization,
                     "settling": settling, "warnings": warnings,
                     "status": "REVIEW" if warnings else "OK"})
    nozzles = {}
    for key, requirements in nozzle_requirements.items():
        required, case_id = max(requirements, key=lambda item: item[0])
        selected = next((pipe for pipe in PIPES if pipe[1] >= required), None) if required else None
        nozzles[key] = {"required_mm": required, "case_id": case_id,
                        "nps": selected[0] if selected else None,
                        "selected_mm": selected[1] if selected else None,
                        "status": "NOT APPLICABLE" if not required else "OK" if selected else "OUT OF RANGE"}
    dp = max(max(case["inputs"]["pressure"] for case in cases) * (1 + c["pressure_margin"]),
             max(case["inputs"]["pressure"] for case in cases) + c["pressure_offset"])
    dt = max(case["inputs"]["temperature"] for case in cases) + c["temperature_offset"]
    p = dp / 10
    se = c["stress"] * c["efficiency"]
    preliminary = {"status": "PRELIMINARY - NOT CODE VERIFIED", "design_pressure_barg": dp,
                   "design_temperature_c": dt, "mdmt_c": c["mdmt"],
                   "material_assumption": "SA-516 Gr. 70N; engineer must confirm allowable stress",
                   "warnings": ["Horizontal vessel and 2:1 ellipsoidal heads only.",
                                "Thickness, weights and hydrotest are estimates, not ASME certification.",
                                "Flange class is ambient indicative only; verify material/temperature rating.",
                                "Hydrotest stress ratio is assumed to be 1; relief sizing is excluded.",
                                "Inventory uses simplified flow-weighted density and head volume."]}
    if dp <= 0 or se - .6 * p <= 0 or 2 * se - .2 * p <= 0 or dt < c["mdmt"]:
        preliminary["warnings"].append("Mechanical estimate inputs are outside supported bounds; estimates withheld.")
    else:
        shell = rounded(max(p * diameter * 1000 / 2 / (se - .6 * p) + c["corrosion"], c["min_thickness"]), 1)
        head = rounded(max(p * diameter * 1000 / (2 * se - .2 * p) + c["corrosion"], c["min_thickness"]), 1)
        volume = area * length + 2 * math.pi * diameter ** 3 / 24
        empty = (math.pi * (diameter + shell / 1000) * shell / 1000 * length * c["steel_density"] + 2 * 1.084 * (diameter + head / 1000) ** 2 * head / 1000 * c["steel_density"]) * (1 + c["weight_margin"])
        density = (governing["oil_design"] * (governing["inputs"]["oil_density"] or 0) + governing["water_design"] * (governing["inputs"]["water_density"] or 0)) / (governing["oil_design"] + governing["water_design"])
        preliminary.update({"shell_mm": shell, "head_mm": head, "internal_volume_m3": volume,
                            "empty_kg": empty, "operating_kg": empty + segment(c["nll"]) * volume * density,
                            "hydrotest_kg": empty + volume * 1000, "hydrotest_barg": c["hydro_factor"] * dp,
                            "ambient_flange_class": "150#" if dp <= 19 else "300#" if dp <= 49 else "600#" if dp <= 99 else "900# (not verified)"})
    result = {"version": VERSION, "status": "REVIEW" if any(case["warnings"] for case in cases) or any(n["status"] == "OUT OF RANGE" for n in nozzles.values()) else "OK",
              "governing_case_id": governing["id"], "governing_case": governing["name"],
              "diameter_m": diameter, "length_m": length, "effective_length_m": effective,
              "criteria": c, "cases": cases, "nozzles": nozzles, "preliminary": preliminary,
              "levels_mm": {key: c[key] * diameter * 1000 for key in ("hhll", "hll", "nll", "lll", "llll")},
              "qualification": "Engineering aid only; not approved design. Case-adjustable NIL. All submitted cases checked."}
    def ensure_finite(item):
        if isinstance(item, dict):
            for val in item.values():
                ensure_finite(val)
        elif isinstance(item, list):
            for val in item:
                ensure_finite(val)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValueError("Inputs exceed supported numerical bounds.")
    ensure_finite(result)
    if payload.get("execution_mode") == "automatic_preview":
        result["execution_mode"] = "automatic_preview"
        result["qualification"] = "Unapproved automatic engineering preview using current criteria and conversion conventions. " + result["qualification"]
    return result
