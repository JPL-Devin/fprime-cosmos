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
    monkeypatch.setattr(launcher, "bridge_command", lambda: ["/bin/fprime-comm-bridge"])
    monkeypatch.setattr(launcher, "docker_source_addresses", lambda: [])
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


def test_bridge_defaults_for_local_docker_cosmos(monkeypatch):
    monkeypatch.setattr(launcher, "docker_source_addresses", lambda: ["172.18.0.6", "172.17.0.1"])
    assert launcher.bridge_defaults("http://localhost:2900", []) == [
        "--udp-fast-bind-address",
        "0.0.0.0",
        "--udp-fast-allowed-source",
        "172.18.0.6",
        "172.17.0.1",
    ]
    assert launcher.bridge_defaults("http://localhost:2900", ["--udp-fast-allowed-source", "10.0.0.1"]) == []
    assert launcher.bridge_defaults("http://cosmos.example.invalid:2900", []) == []


def test_bridge_defaults_without_docker(monkeypatch):
    monkeypatch.setattr(launcher, "docker_source_addresses", lambda: [])
    assert launcher.bridge_defaults("http://127.0.0.1:2900", []) == []


def test_docker_gateway_addresses(monkeypatch):
    output = (
        "1: lo    inet 127.0.0.1/8 scope host lo\n"
        "3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\n"
        "4: br-5    inet 172.18.0.1/16 brd 172.18.255.255 scope global br-5\n"
    )
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": output})())
    assert launcher.docker_gateway_addresses() == ["172.17.0.1", "172.18.0.1"]


def test_bridge_command_prefers_current_interpreter(monkeypatch):
    monkeypatch.setattr(launcher.importlib.util, "find_spec", lambda name: object())
    assert launcher.bridge_command() == [launcher.sys.executable, "-m", launcher.BRIDGE_MODULE]


def test_bridge_command_falls_back_to_path(monkeypatch):
    monkeypatch.setattr(launcher.importlib.util, "find_spec", lambda name: None)
    monkeypatch.setattr(launcher.shutil, "which", lambda name: None)
    assert launcher.bridge_command() is None


def test_docker_container_addresses(monkeypatch):
    outputs = iter(["abc\ndef\n", "172.18.0.6\n\n172.18.0.7\n"])
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": next(outputs)})())
    assert launcher.docker_container_addresses() == ["172.18.0.6", "172.18.0.7"]


def test_docker_source_addresses_without_docker(monkeypatch):
    def fail(*a, **k):
        raise OSError("no docker")

    monkeypatch.setattr(launcher.subprocess, "run", fail)
    assert launcher.docker_source_addresses() == []
