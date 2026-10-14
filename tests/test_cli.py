import pytest

from tests.conftest import REPO_CONFIG
from trademare.cli import main


def test_check_command_succeeds(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["-c", str(REPO_CONFIG), "check"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "config OK" in out and "[IN]" in out and "[US]" in out


def test_check_command_reports_bad_config(tmp_path, capsys):
    bad = tmp_path / "settings.yaml"
    bad.write_text("markets: {IN: {broker: alpaca, universe: x, budget: 1}}")
    with pytest.raises(SystemExit) as exit_info:
        main(["-c", str(bad), "check"])
    assert exit_info.value.code == 1
    assert "must be one of" in capsys.readouterr().err
