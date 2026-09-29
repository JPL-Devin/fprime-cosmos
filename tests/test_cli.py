"""Command line entry points"""

import pytest

from fprime_openc3 import __main__ as launcher
from fprime_openc3.generate.__main__ import main as generate_main
from tests.conftest import REFERENCE_DICTIONARY


def test_generate_cli(tmp_path, capsys):
    assert generate_main([str(REFERENCE_DICTIONARY), "-o", str(tmp_path), "--no-gem"]) == 0
    assert (tmp_path / "openc3-cosmos-fprime-yamcsdeployment" / "plugin.txt").is_file()
    assert "Plugin written" in capsys.readouterr().out


def test_generate_cli_bad_dictionary(tmp_path, capsys):
    assert generate_main([str(tmp_path / "nope.json"), "-o", str(tmp_path)]) == 1
    assert "[ERROR]" in capsys.readouterr().err


def test_parse_variables():
    assert launcher.parse_variables(["a=1", "b=x=y"]) == {"a": "1", "b": "x=y"}
    with pytest.raises(ValueError):
        launcher.parse_variables(["novalue"])


def test_launcher_skip_everything(tmp_path, capsys):
    argv = ["--dictionary", str(REFERENCE_DICTIONARY), "--output", str(tmp_path), "--skip-install", "--skip-bridge"]
    assert launcher.main(argv) == 0
    assert list(tmp_path.glob("*.gem"))


def test_launcher_forwards_bridge_arguments(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(launcher.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(launcher.subprocess, "call", lambda command: calls.append(command) or 0)
    argv = [
        "--dictionary",
        str(REFERENCE_DICTIONARY),
        "--output",
        str(tmp_path),
        "--skip-install",
        "--communication-selection",
        "udp",
    ]
    assert launcher.main(argv) == 0
    (command,) = calls
    assert command[0] == "/bin/fprime-comm-bridge"
    assert command[1:3] == ["--dictionary", str(REFERENCE_DICTIONARY.resolve())]
    assert command[3:5] == ["--framing-selection", launcher.DEFAULT_FRAMING]
    assert command[5:] == ["--communication-selection", "udp"]
