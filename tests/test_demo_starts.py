"""Each demo persona starts incomplete and ends with a complete expected profile."""

from __future__ import annotations

import json
from pathlib import Path

DATASETS = Path(__file__).resolve().parents[1] / "eval" / "synthetic-datasets"
DEMO = json.loads((DATASETS / "demo-set.json").read_text(encoding="utf-8"))
STARTS = {
    item["profile_id"]: item
    for item in json.loads(
        (DATASETS / "starter-profiles.json").read_text(encoding="utf-8")
    )["personas"]
}
ENDS = {
    item["profile_id"]: item
    for item in json.loads(
        (DATASETS / "expected-outcomes.json").read_text(encoding="utf-8")
    )["personas"]
}


def test_every_demo_persona_has_an_opening_question_and_a_starter_profile() -> None:
    """The conversation has something to ask and something to start from."""
    for entry in DEMO["personas"]:
        start = STARTS[entry["profile_id"]]
        assert start["opening_question"].strip()
        assert start["starter_profile"]["profile_id"] == entry["profile_id"]


def test_starter_profiles_hold_fewer_facts_than_the_expected_end_state() -> None:
    """A start is deliberately incomplete; the end state is the full profile."""
    for entry in DEMO["personas"]:
        profile_id = entry["profile_id"]
        start = STARTS[profile_id]["starter_profile"]
        end = ENDS[profile_id]["expected_end_state_profile"]
        assert len(start) <= len(end), profile_id
