"""Command line entry points"""

import pytest

from fprime_cosmos import __main__ as launcher
from fprime_cosmos.generate import __main__ as generate
from fprime_cosmos.generate.__main__ import main as generate_main
from tests.conftest import REFERENCE_DICTIONARY


def test_generate_cli(tmp_path, capsys):
    assert generate_main([str(REFERENCE_DICTIONARY), "-o", str(tmp_path), "--no-gem"]) == 0
    assert (tmp_path / "openc3-cosmos-fprime-yamcsdeployment" / "plugin.txt").is_file()
    assert "Plugin written" in capsys.readouterr().out


def test_generate_cli_bad_dictionary(tmp_path, capsys):
    assert generate_main([str(tmp_path / "nope.json"), "-o", str(tmp_path)]) == 1
    assert "[ERROR]" in capsys.readouterr().err


def test_launcher_skip_everything(tmp_path, capsys):
    argv = ["--dictionary", str(REFERENCE_DICTIONARY), "--output", str(tmp_path), "--skip-install", "--skip-bridge"]
    assert launcher.main(argv) == 0
    assert list(tmp_path.glob("*.gem"))


class FakePopen:
    """Records the command; `poll` reports the exit code after one poll so run_processes returns"""

    started = []

    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.returncode = None
        self.polls = 0
        FakePopen.started.append(self)

    def poll(self):
        self.polls += 1
        if self.polls > 1 and self is FakePopen.started[0]:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def wait(self, timeout=None):
        return self.returncode


@pytest.fixture
def popen(monkeypatch):
    FakePopen.started = []
    monkeypatch.setattr(launcher.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(launcher.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(launcher, "bridge_command", lambda: ["/bin/fprime-comm-bridge"])
    monkeypatch.setattr(launcher, "docker_source_addresses", lambda: [])
    return FakePopen


def test_launcher_forwards_bridge_arguments(tmp_path, popen):
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
    (bridge,) = popen.started
    assert bridge.args[0] == "/bin/fprime-comm-bridge"
    assert bridge.args[1:3] == ["--dictionary", str(REFERENCE_DICTIONARY.resolve())]
    assert bridge.args[3:5] == ["--framing-selection", launcher.DEFAULT_FRAMING]
    assert bridge.args[5:] == ["--communication-selection", "udp"]


def test_launcher_starts_app_after_bridge_and_stops_it(tmp_path, popen):
    app = tmp_path / "Ref"
    app.write_text("#!/bin/sh\n")
    argv = ["--dictionary", str(REFERENCE_DICTIONARY), "--output", str(tmp_path), "--skip-install", "--app", str(app)]
    assert launcher.main(argv + ["--tcp-fast-port", "60000"]) == 0
    bridge, deployment = popen.started
    assert deployment.args == [str(app), "-a", "127.0.0.1", "-p", "60000"]
    assert deployment.kwargs["cwd"] == tmp_path
    assert deployment.returncode == -15, "the deployment is stopped when the bridge exits"
    assert (tmp_path / "logs" / "Ref.log").is_file()


def test_launcher_rejects_missing_app(tmp_path):
    argv = ["--dictionary", str(REFERENCE_DICTIONARY), "--skip-install", "--app", str(tmp_path / "nope")]
    with pytest.raises(SystemExit):
        launcher.main(argv)


def test_option_value():
    assert launcher.option_value(["--tcp-fast-port", "1"], "--tcp-fast-port", "0") == "1"
    assert launcher.option_value(["--tcp-fast-port=2"], "--tcp-fast-port", "0") == "2"
    assert launcher.option_value(["--tcp-fast-port"], "--tcp-fast-port", "0") == "0"


def test_app_command(tmp_path):
    app = tmp_path / "Ref"
    assert launcher.app_command(app, None, []) == [str(app), "-a", "127.0.0.1", "-p", "50000"]
    assert launcher.app_command(app, None, ["--tcp-fast-address", "0.0.0.0"])[2] == "127.0.0.1"
    assert launcher.app_command(app, None, ["--tcp-fast-address", "10.0.0.2"])[2] == "10.0.0.2"
    assert launcher.app_command(app, "-a host -p 1 --extra 'x y'", []) == [
        str(app),
        "-a",
        "host",
        "-p",
        "1",
        "--extra",
        "x y",
    ]


def test_generate_install_requires_gem(tmp_path, monkeypatch):
    installed = []
    monkeypatch.setattr(generate, "install_from_arguments", lambda args, artifacts: installed.append(artifacts))
    assert generate_main([str(REFERENCE_DICTIONARY), "-o", str(tmp_path), "--install"]) == 0
    assert installed[0].gem_path.is_file()
    with pytest.raises(SystemExit):
        generate_main([str(REFERENCE_DICTIONARY), "-o", str(tmp_path), "--install", "--no-gem"])


def test_generate_without_install_does_not_touch_cosmos(tmp_path, monkeypatch):
    def fail(*args):
        raise AssertionError("COSMOS must not be contacted without --install")

    monkeypatch.setattr(generate, "install_from_arguments", fail)
    assert generate_main([str(REFERENCE_DICTIONARY), "-o", str(tmp_path)]) == 0


def test_bridge_defaults_for_local_docker_cosmos(monkeypatch):
    monkeypatch.setattr(launcher, "docker_source_addresses", lambda: ["172.18.0.6", "172.17.0.1"])
    monkeypatch.setattr(launcher, "docker_host_address", lambda: "172.17.0.1")
    assert launcher.bridge_defaults("http://localhost:2900", []) == [
        "--udp-fast-bind-address",
        "172.17.0.1",
        "--udp-fast-allowed-source",
        "172.18.0.6",
        "172.17.0.1",
    ]
    assert launcher.bridge_defaults("http://localhost:2900", ["--udp-fast-allowed-source", "10.0.0.1"]) == []
    assert launcher.bridge_defaults("http://cosmos.example.invalid:2900", []) == []


def test_bridge_defaults_without_docker(monkeypatch):
    monkeypatch.setattr(launcher, "docker_source_addresses", lambda: [])
    assert launcher.bridge_defaults("http://127.0.0.1:2900", []) == []


def test_bridge_defaults_bind_any_without_docker0(monkeypatch):
    monkeypatch.setattr(launcher, "docker_source_addresses", lambda: ["172.18.0.6"])
    monkeypatch.setattr(launcher, "docker_host_address", lambda: None)
    assert launcher.bridge_defaults("http://localhost:2900", [])[:2] == ["--udp-fast-bind-address", "0.0.0.0"]


def test_docker_gateway_addresses(monkeypatch):
    output = (
        "1: lo    inet 127.0.0.1/8 scope host lo\n"
        "3: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\n"
        "4: br-5    inet 172.18.0.1/16 brd 172.18.255.255 scope global br-5\n"
    )
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": output})())
    assert launcher.docker_gateway_addresses() == ["172.17.0.1", "172.18.0.1"]
    assert launcher.docker_host_address() == "172.17.0.1"


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
