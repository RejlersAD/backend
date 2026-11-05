import copy
import json
import math
import unittest

from apps.process_datasheet.services.separator_sizing import CRITERIA, calculate, interface_height, segment


def example_payload():
    rows = [
        ("Max Oil", 60, 30, 20, .92, 200, 820, 3, 50, 1030, .5),
        ("Max Water", 65, 25, 20.5, .92, 120, 825, 3.5, 220, 1035, .48),
        ("Max Gas", 55, 60, 19.5, .91, 150, 815, 2.8, 80, 1030, .52),
    ]
    return {"basis_confirmed": True, "criteria": {key: spec[2] for key, spec in CRITERIA.items()},
            "cases": [{"id": str(index), "name": row[0], "inputs": dict(zip(
                ("pressure", "temperature", "gas_flow", "gas_mw", "gas_z", "gas_viscosity",
                 "oil_flow", "oil_density", "oil_viscosity", "water_flow", "water_density", "water_viscosity"),
                (20, row[1], row[2], row[3], row[4], .012, *row[5:]),
            ))} for index, row in enumerate(rows)]}


class SeparatorSizingTests(unittest.TestCase):
    def test_automatic_preview_does_not_claim_human_confirmation(self):
        payload = example_payload()
        payload["basis_confirmed"] = False
        payload["execution_mode"] = "automatic_preview"
        result = calculate(payload)
        self.assertEqual(result["execution_mode"], "automatic_preview")
        self.assertIn("Unapproved automatic", result["qualification"])
        payload["cases"][0]["inputs"]["gas_mw"] = None
        with self.assertRaises(ValueError):
            calculate(payload)

    def test_workbook_reference_and_qualification(self):
        payload = example_payload()
        original = copy.deepcopy(payload)
        result = calculate(payload)
        self.assertEqual(payload, original)
        self.assertEqual(result["governing_case"], "Max Water")
        self.assertAlmostEqual(result["diameter_m"], 2.8)
        self.assertAlmostEqual(result["length_m"], 11.2)
        expected = [
            (2.44157711435959, .425451207045442, 385.576168557857, 8.17633293447499),
            (2.70510056421297, .360546914574679, 1102.01420451991, 6.54388822412498),
            (None, .831924470886398, None, 9.90177724596665),
        ]
        for case, values in zip(result["cases"], expected):
            for key, val in zip(("required_id", "gas_utilization", "nil_mm", "oil_retention"), values):
                if val is not None:
                    self.assertAlmostEqual(case[key], val, places=9)
            self.assertEqual(case["status"], "REVIEW")
            self.assertGreater(case["settling"]["oil_re"], 2)
        self.assertEqual(result["status"], "REVIEW")
        self.assertEqual(result["nozzles"]["inlet"]["nps"], 18)
        self.assertAlmostEqual(result["nozzles"]["inlet"]["required_mm"], 397.407578772962, places=9)
        self.assertEqual(result["preliminary"]["shell_mm"], 26)
        self.assertAlmostEqual(result["preliminary"]["empty_kg"], 28594.2688685976, places=7)
        self.assertAlmostEqual(result["preliminary"]["operating_kg"], 73567.753822243, places=7)
        json.dumps(result, allow_nan=False)

    def test_strict_input_validation(self):
        for key, val in [("oil_flow", -1), ("gas_z", 0), ("oil_viscosity", None),
                         ("temperature", -273.15), ("pressure", -2), ("gas_mw", True),
                         ("oil_density", float("nan")), ("water_flow", "220")]:
            with self.subTest(key=key, val=val):
                payload = example_payload()
                payload["cases"][0]["inputs"][key] = val
                with self.assertRaises(ValueError):
                    calculate(payload)

    def test_density_ordering(self):
        payload = example_payload()
        payload["cases"][0]["inputs"]["water_density"] = 800
        with self.assertRaisesRegex(ValueError, "water density"):
            calculate(payload)

    def test_all_cases_govern_not_only_flow_maxima(self):
        payload = example_payload()
        extra = copy.deepcopy(payload["cases"][0])
        extra.update(id="low-pressure", name="Lower-flow low-pressure case")
        extra["inputs"].update(pressure=0, gas_flow=20, oil_flow=100, water_flow=20)
        payload["cases"].append(extra)
        result = calculate(payload)
        self.assertEqual(result["governing_case_id"], "low-pressure")

    def test_tie_is_first_case(self):
        payload = example_payload()
        payload["cases"] = [payload["cases"][0], copy.deepcopy(payload["cases"][0])]
        payload["cases"][1]["id"] = "tie"
        self.assertEqual(calculate(payload)["governing_case_id"], "0")

    def test_minimum_controls(self):
        payload = example_payload()
        payload["criteria"]["min_id"] = 5
        self.assertTrue(all(case["control"] == "Minimum ID" for case in calculate(payload)["cases"]))

    def test_zero_and_absent_phases(self):
        for phase in ("gas", "oil", "water"):
            payload = example_payload()
            payload["cases"] = [payload["cases"][0]]
            x = payload["cases"][0]["inputs"]
            x[f"{phase}_flow"] = 0
            for key in list(x):
                if key.startswith(phase + "_") and key != phase + "_flow":
                    x[key] = None
            result = calculate(payload)
            self.assertEqual(result["nozzles"][phase]["status"], "NOT APPLICABLE")
            if phase != "gas":
                self.assertIsNone(result["cases"][0]["nil_mm"])
            json.dumps(result, allow_nan=False)

    def test_gas_only_and_no_flow_rejected(self):
        for gas in (0, 30):
            payload = example_payload()
            payload["cases"][0]["inputs"].update(oil_flow=0, water_flow=0, gas_flow=gas)
            with self.assertRaises(ValueError):
                calculate(payload)

    def test_nozzle_out_of_range_is_not_capped(self):
        payload = example_payload()
        payload["criteria"]["oil_velocity"] = .001
        nozzle = calculate(payload)["nozzles"]["oil"]
        self.assertGreater(nozzle["required_mm"], 893.8)
        self.assertEqual(nozzle["status"], "OUT OF RANGE")
        self.assertIsNone(nozzle["selected_mm"])
        self.assertIsNone(nozzle["nps"])

    def test_validated_range_can_pass_and_invalid_levels_require_review(self):
        payload = example_payload()
        payload["cases"] = payload["cases"][:1]
        payload["criteria"]["oil_drop"] = 100
        self.assertEqual(calculate(payload)["status"], "OK")
        payload["criteria"]["interface_trip"] = 2000
        result = calculate(payload)
        self.assertEqual(result["status"], "REVIEW")
        self.assertTrue(any("Interface" in warning for case in result["cases"] for warning in case["warnings"]))

    def test_solver_residual_and_domain(self):
        for fraction in (0, 1e-10, .01, .365527592666563, .99, 1):
            self.assertLessEqual(abs(segment(interface_height(fraction)) - fraction), 1e-12)
        for invalid in (-.1, 1.1):
            with self.assertRaises(ValueError):
                interface_height(invalid)

    def test_criteria_and_confirmation_required(self):
        for mutation in ("confirmation", "levels", "unknown", "blank", "infinite"):
            payload = example_payload()
            if mutation == "confirmation":
                payload["basis_confirmed"] = False
            elif mutation == "levels":
                payload["criteria"]["llll"] = .9
            elif mutation == "unknown":
                payload["criteria"]["orientation"] = "Vertical"
            elif mutation == "blank":
                payload["criteria"]["id_step"] = None
            else:
                payload["criteria"]["stress"] = math.inf
            with self.assertRaises(ValueError):
                calculate(payload)


class SeparatorAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from django.conf import settings
        if not settings.configured:
            settings.configure(SECRET_KEY="separator-test-only", USE_I18N=False, REST_FRAMEWORK={
                "UNAUTHENTICATED_USER": None, "DEFAULT_AUTHENTICATION_CLASSES": [],
            })
        from rest_framework.test import APIRequestFactory
        from apps.process_datasheet.separator_view import separator_sizing_view
        cls.factory = APIRequestFactory()
        cls.view = staticmethod(separator_sizing_view)

    def request(self, method, user=None, data=None, allowed=True):
        from types import SimpleNamespace
        from unittest.mock import patch
        from rest_framework.test import force_authenticate
        request = getattr(self.factory, method)("/", data or {}, format="json")
        if user is not None:
            force_authenticate(request, user=SimpleNamespace(is_authenticated=True, is_superuser=user))
        with patch("apps.process_datasheet.separator_view.user_has_module_access", return_value=allowed):
            return self.view(request)

    def test_unauthenticated_denied(self):
        self.assertIn(self.request("post", data=example_payload()).status_code, (401, 403))

    def test_module_denied(self):
        self.assertEqual(self.request("post", user=False, data=example_payload(), allowed=False).status_code, 403)

    def test_schema_and_calculation(self):
        self.assertEqual(self.request("get", user=False).status_code, 200)
        response = self.request("post", user=False, data=example_payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "REVIEW")

    def test_validation_error_is_explicit(self):
        response = self.request("post", user=False, data={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("Confirm", response.data["error"])
