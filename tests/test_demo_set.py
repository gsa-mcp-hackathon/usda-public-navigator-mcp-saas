"""The product-demo test set: positive, negative and edge personas per scenario."""

from __future__ import annotations

import json
from pathlib import Path

DATASETS = Path(__file__).resolve().parents[1] / "eval" / "synthetic-datasets"
DEMO = json.loads((DATASETS / "demo-set.json").read_text(encoding="utf-8"))
OUTCOMES = json.loads(
    (DATASETS / "expected-outcomes.json").read_text(encoding="utf-8")
)["personas"]
BY_ID = {item["profile_id"]: item for item in OUTCOMES}


def test_every_demo_persona_exists_with_the_declared_scenario_and_case_type() -> None:
    """Each demo entry names a real persona and matches its scenario and case."""
    for entry in DEMO["personas"]:
        persona = BY_ID[entry["profile_id"]]
        assert persona["scenario"].split(" ")[0] == entry["scenario"]
        assert persona["case_type"] == entry["case_type"]


def test_demo_set_stays_small_and_covers_every_scenario() -> None:
    """The set stays small and every scenario has at least one persona."""
    scenarios = {entry["scenario"] for entry in DEMO["personas"]}
    assert scenarios == {
        "UC1-S1",
        "UC1-S2",
        "UC1-S3",
        "UC2-S1",
        "UC2-S2",
        "UC2-S3",
        "UC3-S1",
        "UC3-S2",
        "UC3-S3",
    }
    assert len(DEMO["personas"]) <= 15


def test_scenarios_with_a_positive_persona_also_carry_a_negative_one() -> None:
    """A scenario that shows a positive case also shows the matching negative."""
    cases: dict[str, set[str]] = {}
    for entry in DEMO["personas"]:
        cases.setdefault(entry["scenario"], set()).add(entry["case_type"])
    for scenario, kinds in cases.items():
        if "positive" in kinds:
            assert "negative" in kinds, scenario


def test_demo_personas_carry_synthetic_expected_outcomes() -> None:
    """Every demo persona is synthetic and has an expected end-state profile."""
    for entry in DEMO["personas"]:
        persona = BY_ID[entry["profile_id"]]
        assert persona["profile_id"].startswith("synthetic-")
        assert persona["expected_end_state_profile"]
