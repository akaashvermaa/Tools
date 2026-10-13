import importlib.util
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture()
def launch(tmp_path, monkeypatch):
    """launch.py loaded fresh, pointed at an empty temporary project folder."""
    spec = importlib.util.spec_from_file_location("aura_launch_under_test", os.path.join(ROOT, "launch.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    venv_dir = tmp_path / ".venv"
    monkeypatch.setattr(mod, "ROOT", str(tmp_path))
    monkeypatch.setattr(mod, "VENV_DIR", str(venv_dir))
    monkeypatch.setattr(mod, "VENV_PYTHON", str(venv_dir / "bin" / "python"))
    monkeypatch.setattr(mod, "MARKER", str(venv_dir / ".marker"))
    monkeypatch.setattr(mod, "OPTIONAL_MARKER", str(venv_dir / ".optional"))
    monkeypatch.setattr(mod, "OPTIONAL_REQUIREMENTS", str(tmp_path / "requirements-optional.txt"))
    monkeypatch.setattr("builtins.input", lambda *_: "")
    return mod


# ── command-line options ────────────────────────────────────────────────────

def test_options_set_the_apps_environment(launch, monkeypatch):
    for name in ("AURA_NO_BROWSER", "AURA_PORT"):
        monkeypatch.setenv(name, "")
    launch.apply_options(["--no-browser", "--port", "8080", "--update"])
    assert os.environ["AURA_NO_BROWSER"] == "1" and os.environ["AURA_PORT"] == "8080"


@pytest.mark.parametrize("args", [["--port"], ["--port", "abc"], ["--port", "0"], ["--port", "70000"]])
def test_bad_port_is_explained(launch, capsys, args):
    with pytest.raises(SystemExit) as exc:
        launch.apply_options(args)
    assert exc.value.code == 1 and "--port needs a number" in capsys.readouterr().out


def test_help_prints_usage_and_exits_cleanly(launch, capsys):
    with pytest.raises(SystemExit) as exc:
        launch.apply_options(["--help"])
    assert exc.value.code == 0 and "--no-browser" in capsys.readouterr().out


def test_fail_never_blocks_without_a_keyboard(launch, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "stdin", None)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("must not wait for a key press"))
    with pytest.raises(SystemExit):
        launch.fail("boom")
    assert "boom" in capsys.readouterr().out


# ── virtual environment problems ────────────────────────────────────────────

@pytest.mark.parametrize("platform, expected", [
    ("linux", "python3-venv"),
    ("darwin", "python.org"),
    ("win32", "pip"),
])
def test_venv_failure_gives_advice_for_this_os_and_cleans_up(launch, monkeypatch, capsys, platform, expected):
    monkeypatch.setattr(sys, "platform", platform)

    def broken(path, **kwargs):
        os.makedirs(os.path.join(path, "bin"))  # a half-made environment
        raise subprocess.CalledProcessError(1, "ensurepip")

    monkeypatch.setattr(launch.venv, "create", broken)
    with pytest.raises(SystemExit):
        launch.ensure_venv()
    assert expected in capsys.readouterr().out
    assert not os.path.exists(launch.VENV_DIR)          # nothing half-made left behind


def test_venv_without_pip_is_refused_and_removed(launch, monkeypatch, capsys):
    monkeypatch.setattr(launch.venv, "create", lambda path, **k: os.makedirs(os.path.join(path, "bin")))
    monkeypatch.setattr(launch, "venv_has_pip", lambda: False)
    with pytest.raises(SystemExit):
        launch.ensure_venv()
    assert "without pip" in capsys.readouterr().out and not os.path.exists(launch.VENV_DIR)


def test_a_healthy_venv_is_left_alone(launch, monkeypatch):
    os.makedirs(os.path.dirname(launch.VENV_PYTHON))
    open(launch.VENV_PYTHON, "w").close()
    open(launch.MARKER, "w").write("x")                  # requirements installed once already
    monkeypatch.setattr(launch.venv, "create", lambda *a, **k: pytest.fail("must not recreate"))
    launch.ensure_venv()


def test_a_python_without_pip_from_an_earlier_failed_run_is_rebuilt(launch, monkeypatch):
    os.makedirs(os.path.dirname(launch.VENV_PYTHON))
    open(launch.VENV_PYTHON, "w").close()                # no marker: setup never finished
    calls = []
    monkeypatch.setattr(launch, "venv_has_pip", lambda: len(calls) > 0)  # broken first, fine after rebuilding
    monkeypatch.setattr(launch.venv, "create", lambda path, **k: calls.append(k))
    launch.ensure_venv()
    assert calls and calls[0]["clear"] is True


# ── optional extras ─────────────────────────────────────────────────────────

def _optional_setup(launch, monkeypatch, tmp_path, returncode):
    os.makedirs(launch.VENV_DIR)
    (tmp_path / "requirements-optional.txt").write_text("pillow-heif\n")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncode, "", "")

    monkeypatch.setattr(launch.subprocess, "run", fake_run)
    return calls


def test_failed_optional_install_does_not_stop_the_app(launch, monkeypatch, tmp_path, capsys):
    calls = _optional_setup(launch, monkeypatch, tmp_path, returncode=1)
    launch.ensure_optional()                               # must not raise or exit
    assert "optional extras couldn't be installed" in capsys.readouterr().out and len(calls) == 1
    launch.ensure_optional()                               # not retried on every start...
    assert len(calls) == 1
    launch.ensure_optional(force=True)                     # ...unless --update asks for it
    assert len(calls) == 2


def test_successful_optional_install_is_quiet_and_remembered(launch, monkeypatch, tmp_path, capsys):
    calls = _optional_setup(launch, monkeypatch, tmp_path, returncode=0)
    launch.ensure_optional()
    launch.ensure_optional()
    assert "couldn't be installed" not in capsys.readouterr().out and len(calls) == 1


def test_changing_the_optional_list_triggers_a_new_attempt(launch, monkeypatch, tmp_path):
    calls = _optional_setup(launch, monkeypatch, tmp_path, returncode=0)
    launch.ensure_optional()
    (tmp_path / "requirements-optional.txt").write_text("pillow-heif\nsomething-new\n")
    launch.ensure_optional()
    assert len(calls) == 2


def test_requirements_files_are_split_correctly():
    required = open(os.path.join(ROOT, "requirements.txt")).read().lower()
    optional = open(os.path.join(ROOT, "requirements-optional.txt")).read().lower()
    assert "pillow-heif" in optional and "pillow-heif" not in required
    for must_have in ("flask", "pillow", "pymupdf", "pypdf", "yt-dlp"):
        assert must_have in required


# ── the shell launchers ─────────────────────────────────────────────────────

def test_shell_scripts_are_unix_format():
    for name in ("run.sh", "run.command"):
        data = open(os.path.join(ROOT, name), "rb").read()
        assert data.startswith(b"#!/usr/bin/env bash\n") and b"\r" not in data, f"{name} must use Unix line endings"


def test_gitattributes_keeps_launchers_unix_and_bat_windows():
    text = open(os.path.join(ROOT, ".gitattributes")).read()
    assert "*.sh" in text and "*.command" in text and "eol=lf" in text and "*.bat" in text and "eol=crlf" in text


def test_gitignore_covers_the_usual_suspects():
    lines = {ln.strip() for ln in open(os.path.join(ROOT, ".gitignore")) if ln.strip() and not ln.startswith("#")}
    for needed in (".venv/", "__pycache__/", "/tools/", "/build/", "/dist/", ".DS_Store", ".pytest_cache/", "._*", "*~"):
        assert needed in lines, needed
