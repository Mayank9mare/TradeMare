from pathlib import Path

import pytest
import yaml

REPO_CONFIG = Path(__file__).parent.parent / "config" / "settings.yaml"


@pytest.fixture
def base_config_dict() -> dict:
    return yaml.safe_load(REPO_CONFIG.read_text())


@pytest.fixture
def write_config(tmp_path):
    """Write a config dict to a temp settings.yaml (and optional local override)."""

    def _write(data: dict, local: dict | None = None) -> Path:
        path = tmp_path / "settings.yaml"
        path.write_text(yaml.safe_dump(data))
        if local is not None:
            (tmp_path / "settings.local.yaml").write_text(yaml.safe_dump(local))
        return path

    return _write
