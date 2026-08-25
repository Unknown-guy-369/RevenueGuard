from __future__ import annotations

import copy
import json
import re
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "docs" / "contracts" / "v1"
EXAMPLES = CONTRACTS / "examples"
ADRS = ROOT / "docs" / "adr"

CONTRACT_FILES = {
    "revenue-risk-event": CONTRACTS / "revenue-risk-event.schema.json",
    "recovery-case": CONTRACTS / "recovery-case.schema.json",
    "decision-receipt": CONTRACTS / "decision-receipt.schema.json",
    "recovery-action": CONTRACTS / "recovery-action.schema.json",
    "verified-outcome": CONTRACTS / "verified-outcome.schema.json",
}

EXPECTED_ADRS = {
    "0001-postgresql-source-of-truth.md",
    "0002-at-least-once-durable-queue.md",
    "0003-bounded-agent-authority.md",
    "0004-idempotent-outbox-business-effects.md",
    "0005-authoritative-outcome-verification.md",
    "0006-synthetic-evaluation-disclosure.md",
}


class ContractValidationError(AssertionError):
    """Raised when an example violates the supported JSON Schema subset."""


def load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ContractValidationError(f"{path} must contain a JSON object")
    return value


def resolve_ref(root_schema: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise ContractValidationError(f"unsupported non-local $ref: {reference}")
    value: Any = root_schema
    for part in reference[2:].split("/"):
        value = value[part.replace("~1", "/").replace("~0", "~")]
    if not isinstance(value, dict):
        raise ContractValidationError(f"$ref does not resolve to an object: {reference}")
    return value


def matches_type(value: Any, expected: str) -> bool:
    type_checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    return type_checks[expected](value)


def validate_json_schema_subset(
    value: Any,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    path: str = "$",
) -> None:
    if "$ref" in schema:
        validate_json_schema_subset(value, resolve_ref(root_schema, schema["$ref"]), root_schema, path)
        return

    if "const" in schema and value != schema["const"]:
        raise ContractValidationError(f"{path}: expected constant {schema['const']!r}")

    if "enum" in schema and value not in schema["enum"]:
        raise ContractValidationError(f"{path}: {value!r} is not in {schema['enum']!r}")

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed_types = [expected_type] if isinstance(expected_type, str) else expected_type
        if not any(matches_type(value, item) for item in allowed_types):
            raise ContractValidationError(f"{path}: invalid type for {allowed_types!r}")

    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ContractValidationError(f"{path}: string is shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ContractValidationError(f"{path}: string is longer than maxLength")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise ContractValidationError(f"{path}: string does not match {schema['pattern']}")
        if schema.get("format") == "date-time":
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise ContractValidationError(f"{path}: invalid date-time") from error
            if parsed.tzinfo is None:
                raise ContractValidationError(f"{path}: date-time must include a timezone")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ContractValidationError(f"{path}: value is below minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise ContractValidationError(f"{path}: value is above maximum")

    if isinstance(value, dict):
        required = schema.get("required", [])
        missing = [field for field in required if field not in value]
        if missing:
            raise ContractValidationError(f"{path}: missing required fields {missing!r}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ContractValidationError(f"{path}: unknown fields {unknown!r}")
        for field, field_value in value.items():
            if field in properties:
                validate_json_schema_subset(
                    field_value,
                    properties[field],
                    root_schema,
                    f"{path}.{field}",
                )

    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ContractValidationError(f"{path}: too few items")
        if schema.get("uniqueItems"):
            canonical = [json.dumps(item, sort_keys=True) for item in value]
            if len(canonical) != len(set(canonical)):
                raise ContractValidationError(f"{path}: items must be unique")
        if "items" in schema:
            for index, item in enumerate(value):
                validate_json_schema_subset(item, schema["items"], root_schema, f"{path}[{index}]")

    if "allOf" in schema:
        for index, child_schema in enumerate(schema["allOf"]):
            validate_json_schema_subset(value, child_schema, root_schema, f"{path}.allOf[{index}]")

    if "anyOf" in schema:
        errors = []
        for child_schema in schema["anyOf"]:
            try:
                validate_json_schema_subset(value, child_schema, root_schema, path)
                break
            except ContractValidationError as error:
                errors.append(str(error))
        else:
            raise ContractValidationError(f"{path}: no anyOf branch matched: {errors!r}")

    if "if" in schema:
        try:
            validate_json_schema_subset(value, schema["if"], root_schema, path)
        except ContractValidationError:
            condition_matches = False
        else:
            condition_matches = True
        branch = schema.get("then" if condition_matches else "else")
        if branch is not None:
            validate_json_schema_subset(value, branch, root_schema, path)


class PhaseZeroContractTests(unittest.TestCase):
    def test_all_contract_schemas_have_required_metadata(self) -> None:
        for name, path in CONTRACT_FILES.items():
            with self.subTest(contract=name):
                schema = load_json(path)
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
                self.assertIn("/v1/", schema["$id"])
                self.assertEqual(schema["type"], "object")
                self.assertFalse(schema["additionalProperties"])
                self.assertEqual(schema["properties"]["schema_version"]["const"], "1.0")

    def test_canonical_examples_validate_against_contracts(self) -> None:
        for name, schema_path in CONTRACT_FILES.items():
            with self.subTest(contract=name):
                schema = load_json(schema_path)
                example = load_json(EXAMPLES / f"{name}.example.json")
                validate_json_schema_subset(example, schema, schema)

    def test_money_fields_are_integer_minor_units_with_currency(self) -> None:
        for path in sorted(EXAMPLES.glob("*.json")):
            example = load_json(path)
            for key, value in walk_items(example):
                if key.endswith("_minor"):
                    self.assertIsInstance(value, int, f"{path}: {key} must be an integer")
                    self.assertNotIsInstance(value, bool, f"{path}: {key} cannot be boolean")
                    if key != "expected_net_recovery_minor":
                        self.assertGreaterEqual(value, 0, f"{path}: {key} cannot be negative")
            if "currency" in example:
                self.assertRegex(example["currency"], r"^[A-Z]{3}$")

    def test_state_machine_is_closed_and_terminal_states_have_no_outputs(self) -> None:
        machine = load_json(CONTRACTS / "case-state-machine.json")
        states = set(machine["states"])
        self.assertEqual(machine["schema_version"], "1.0")
        self.assertIn(machine["initial_state"], states)
        self.assertEqual(set(machine["transitions"]), states)
        for source, targets in machine["transitions"].items():
            self.assertTrue(set(targets).issubset(states), f"unknown target from {source}")
        for terminal_state in machine["terminal_states"]:
            self.assertEqual(machine["transitions"][terminal_state], [])

    def test_recovery_case_example_uses_declared_state(self) -> None:
        case = load_json(EXAMPLES / "recovery-case.example.json")
        machine = load_json(CONTRACTS / "case-state-machine.json")
        self.assertIn(case["state"], machine["states"])

        case_schema = load_json(CONTRACT_FILES["recovery-case"])
        receipt_schema = load_json(CONTRACT_FILES["decision-receipt"])
        self.assertEqual(set(case_schema["properties"]["state"]["enum"]), set(machine["states"]))
        self.assertEqual(
            set(receipt_schema["properties"]["resulting_state"]["enum"]),
            set(machine["states"]),
        )

    def test_terminal_case_requires_a_non_empty_reason(self) -> None:
        schema = load_json(CONTRACT_FILES["recovery-case"])
        case = load_json(EXAMPLES / "recovery-case.example.json")
        case["state"] = "STOPPED"
        case["terminal_reason"] = None
        with self.assertRaises(ContractValidationError):
            validate_json_schema_subset(case, schema, schema)

    def test_unknown_outcome_cannot_count_recovered_money(self) -> None:
        schema = load_json(CONTRACT_FILES["verified-outcome"])
        outcome = load_json(EXAMPLES / "verified-outcome.example.json")
        outcome.update(
            {
                "outcome_status": "UNKNOWN",
                "is_authoritative": false_value(),
                "recovered_amount_minor": 1,
                "verified_at": None,
            }
        )
        with self.assertRaises(ContractValidationError):
            validate_json_schema_subset(outcome, schema, schema)

    def test_positive_recovery_requires_authoritative_success(self) -> None:
        schema = load_json(CONTRACT_FILES["verified-outcome"])
        outcome = load_json(EXAMPLES / "verified-outcome.example.json")
        outcome["is_authoritative"] = False
        with self.assertRaises(ContractValidationError):
            validate_json_schema_subset(outcome, schema, schema)

    def test_action_has_stable_business_idempotency_material(self) -> None:
        action = load_json(EXAMPLES / "recovery-action.example.json")
        key = action["idempotency_key"]
        self.assertIn(action["merchant_id"], key)
        self.assertIn(action["case_id"], key)
        self.assertNotIn("worker", key.lower())
        self.assertGreaterEqual(action["logical_attempt"], 1)

    def test_required_adrs_are_accepted(self) -> None:
        actual = {path.name for path in ADRS.glob("[0-9][0-9][0-9][0-9]-*.md")}
        self.assertEqual(actual, EXPECTED_ADRS)
        for filename in EXPECTED_ADRS:
            content = (ADRS / filename).read_text(encoding="utf-8")
            self.assertIn("**Status:** Accepted", content)
            self.assertIn("## Decision", content)
            self.assertIn("## Consequences", content)
            self.assertIn("## Verification", content)

    def test_scope_forbids_direct_agent_execution(self) -> None:
        scope = (ROOT / "docs" / "product" / "PHASE_0_SCOPE.md").read_text(encoding="utf-8")
        self.assertIn("Directly execute Razorpay/contact actions", scope)
        self.assertIn("Only authoritative `SUCCEEDED` outcomes", scope)
        self.assertIn("Default merchant policy is deny-by-default", scope)

    def test_evaluation_contract_is_frozen_and_has_hard_gates(self) -> None:
        criteria = (ROOT / "docs" / "evaluation" / "SUCCESS_CRITERIA.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("**Status:** Pre-registered and frozen", criteria)
        self.assertIn("policy violations                          = 0", criteria)
        self.assertIn("duplicate external business effects        = 0", criteria)
        self.assertIn("unverified amount counted as recovered     = 0", criteria)
        self.assertIn("at least 10 fixed seeds", criteria)

    def test_retired_project_name_is_absent(self) -> None:
        retired_name = "Recovery" + "OS"
        checked_roots = [ROOT / "AGENTS.md", ROOT / "ARCHITECTURE.md", ROOT / "IMPLEMENTATION_PLAN.md", ROOT / "docs"]
        for checked_root in checked_roots:
            paths = [checked_root] if checked_root.is_file() else checked_root.rglob("*")
            for path in paths:
                if path.is_file() and path.suffix in {".md", ".json", ".py"}:
                    self.assertNotIn(retired_name, path.read_text(encoding="utf-8"), str(path))


def walk_items(value: Any):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key, child
            yield from walk_items(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_items(child)


def false_value() -> bool:
    """Keep the unsafe-outcome fixture mutation visually explicit."""
    return False


if __name__ == "__main__":
    unittest.main()
