import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config_loader import CONFIG_PATH_ENV, load_config


def test_load_config_uses_absolute_external_file(tmp_path, monkeypatch):
    external_config = tmp_path / "vm-config.json"
    external_config.write_text(
        json.dumps({"api_container": "vm-api"}), encoding="utf-8"
    )
    monkeypatch.setenv(CONFIG_PATH_ENV, str(external_config))

    assert load_config(tmp_path / "default.json") == {"api_container": "vm-api"}


def test_load_config_rejects_relative_external_file(monkeypatch, tmp_path):
    monkeypatch.setenv(CONFIG_PATH_ENV, "vm-config.json")

    with pytest.raises(ValueError, match="must be an absolute path"):
        load_config(tmp_path / "default.json")


def test_load_config_requires_json_object(tmp_path, monkeypatch):
    external_config = tmp_path / "vm-config.json"
    external_config.write_text("[]", encoding="utf-8")
    monkeypatch.setenv(CONFIG_PATH_ENV, str(external_config))

    with pytest.raises(ValueError, match="JSON object"):
        load_config(tmp_path / "default.json")
