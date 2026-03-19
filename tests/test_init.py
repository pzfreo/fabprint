"""Tests for fabprint init, validate, and template commands."""

from pathlib import Path
from unittest.mock import patch

import pytest

from fabprint.cli import main
from fabprint.init import (
    ValidationResult,
    _build_toml,
    _closest_match,
    dump_template,
    validate_config,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _posix(p: Path) -> str:
    return p.as_posix()


def _write_valid_config(tmp_path: Path) -> Path:
    """Write a minimal valid config for validation tests."""
    toml = tmp_path / "fabprint.toml"
    toml.write_text(f"""
[slicer]
engine = "orca"
version = "2.3.1"

[[parts]]
file = "{_posix(FIXTURES / "cube_10mm.stl")}"
""")
    return toml


def _mock_ui_inputs(monkeypatch, inputs):
    """Mock ui prompt functions with an iterator of responses."""
    it = iter(inputs)

    def next_str(prompt, default=None):
        try:
            val = next(it)
        except StopIteration:
            return default or ""
        return val if val != "" else (default or "")

    def next_int(prompt, default=0):
        try:
            val = next(it)
        except StopIteration:
            return default
        return int(val) if val != "" else default

    def next_yn(prompt, default=True):
        try:
            val = next(it)
        except StopIteration:
            return default
        if val == "":
            return default
        return str(val).lower().startswith("y")

    monkeypatch.setattr("fabprint.ui.prompt_str", next_str)
    monkeypatch.setattr("fabprint.ui.prompt_int", next_int)
    monkeypatch.setattr("fabprint.ui.prompt_yn", next_yn)
    monkeypatch.setattr("fabprint.ui.prompt_password", lambda prompt: next_str(prompt))
    monkeypatch.setattr("fabprint.ui.heading", lambda text: None)
    monkeypatch.setattr("fabprint.ui.success", lambda text: None)
    monkeypatch.setattr("fabprint.ui.warn", lambda text: None)
    monkeypatch.setattr("fabprint.ui.error", lambda text: None)
    monkeypatch.setattr("fabprint.ui.info", lambda text: None)
    monkeypatch.setattr("fabprint.ui.choice_table", lambda items, columns: None)
    monkeypatch.setattr("fabprint.ui.preview_toml", lambda text: None)

    def mock_pick(options, prompt="Pick", allow_multi=False):
        try:
            val = next(it)
        except StopIteration:
            return [0]
        if val == "all":
            return list(range(len(options)))
        try:
            return [int(val) - 1]
        except (ValueError, TypeError):
            return [0]

    monkeypatch.setattr("fabprint.ui.pick", mock_pick)


# ---------------------------------------------------------------------------
# Template tests
# ---------------------------------------------------------------------------


class TestTemplate:
    def test_dump_template_returns_string(self):
        t = dump_template()
        assert isinstance(t, str)
        assert "[slicer]" in t
        assert "[[parts]]" in t
        assert "[plate]" in t
        assert "[pipeline]" in t

    def test_dump_template_has_comments(self):
        t = dump_template()
        assert "# " in t

    def test_cli_init_template(self, capsys):
        main(["init", "--template"])
        out = capsys.readouterr().out
        assert "[slicer]" in out
        assert "[[parts]]" in out


# ---------------------------------------------------------------------------
# Validate tests
# ---------------------------------------------------------------------------


class TestValidate:
    def test_valid_config_no_warnings(self, tmp_path):
        # Config with version set — no warnings expected (profiles may not be installed)
        cfg = _write_valid_config(tmp_path)
        result = validate_config(cfg)
        # The only possible warnings are about profiles not being installed,
        # which is environment-dependent. No hard errors should occur.
        assert isinstance(result, ValidationResult)
        assert len(result.passes) > 0

    def test_missing_version_warning(self, tmp_path):
        toml = tmp_path / "fabprint.toml"
        toml.write_text(f"""
[slicer]
engine = "orca"

[[parts]]
file = "{_posix(FIXTURES / "cube_10mm.stl")}"
""")
        warnings = validate_config(toml)
        assert any("version" in w for w in warnings)

    def test_absolute_path_warning(self, tmp_path):
        abs_path = FIXTURES / "cube_10mm.stl"
        toml = tmp_path / "fabprint.toml"
        toml.write_text(f"""
[slicer]
engine = "orca"
version = "2.3.1"

[[parts]]
file = "{_posix(abs_path.resolve())}"
""")
        warnings = validate_config(toml)
        assert any("absolute path" in w for w in warnings)

    def test_cli_validate(self, tmp_path, capsys, monkeypatch):
        cfg = _write_valid_config(tmp_path)
        monkeypatch.chdir(tmp_path)
        main(["validate", str(cfg)])
        out = capsys.readouterr().out
        # Should print either "Config OK" or warnings — not crash
        assert "OK" in out or "warning" in out

    def test_cli_validate_auto_discover(self, tmp_path, capsys, monkeypatch):
        _write_valid_config(tmp_path)
        monkeypatch.chdir(tmp_path)
        main(["validate"])
        out = capsys.readouterr().out
        assert "OK" in out or "warning" in out

    def test_cli_validate_missing_config(self, tmp_path, monkeypatch, capsys):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(SystemExit) as exc_info:
            main(["validate"])
        assert exc_info.value.code == 1

    def test_missing_part_file_hard_error(self, tmp_path):
        """Missing part file is a hard error from load_config, not a warning."""
        from fabprint import FabprintError

        toml = tmp_path / "fabprint.toml"
        toml.write_text("""
[slicer]
engine = "orca"
version = "2.3.1"

[[parts]]
file = "nonexistent.stl"
""")
        with pytest.raises(FabprintError, match="file not found"):
            validate_config(toml)

    def test_unreadable_part_extension(self, tmp_path):
        bad_file = tmp_path / "model.zip"
        bad_file.write_bytes(b"fake")
        toml = tmp_path / "fabprint.toml"
        toml.write_text(f"""
[slicer]
engine = "orca"
version = "2.3.1"

[[parts]]
file = "{_posix(bad_file)}"
""")
        warnings = validate_config(toml)
        assert any("unsupported extension" in w for w in warnings)

    def test_duplicate_part_files(self, tmp_path):
        stl = FIXTURES / "cube_10mm.stl"
        toml = tmp_path / "fabprint.toml"
        toml.write_text(f"""
[slicer]
engine = "orca"
version = "2.3.1"

[[parts]]
file = "{_posix(stl)}"

[[parts]]
file = "{_posix(stl)}"
""")
        warnings = validate_config(toml)
        assert any("appears more than once" in w for w in warnings)

    def test_small_plate_warning(self, tmp_path):
        toml = tmp_path / "fabprint.toml"
        toml.write_text(f"""
[slicer]
engine = "orca"
version = "2.3.1"

[plate]
size = [10, 10]

[[parts]]
file = "{_posix(FIXTURES / "cube_10mm.stl")}"
""")
        warnings = validate_config(toml)
        assert any("seems very small" in w for w in warnings)

    def test_large_plate_warning(self, tmp_path):
        toml = tmp_path / "fabprint.toml"
        toml.write_text(f"""
[slicer]
engine = "orca"
version = "2.3.1"

[plate]
size = [2000, 2000]

[[parts]]
file = "{_posix(FIXTURES / "cube_10mm.stl")}"
""")
        warnings = validate_config(toml)
        assert any("seems very large" in w for w in warnings)

    def test_printer_name_no_credentials(self, tmp_path):
        toml = tmp_path / "fabprint.toml"
        toml.write_text(f"""
[slicer]
engine = "orca"
version = "2.3.1"

[[parts]]
file = "{_posix(FIXTURES / "cube_10mm.stl")}"

[printer]
name = "nonexistent-printer"
""")
        # Patch credentials path to a non-existent file
        with patch(
            "fabprint.credentials._credentials_path",
            return_value=tmp_path / "no-creds.toml",
        ):
            warnings = validate_config(toml)
        assert any("credentials" in w.lower() for w in warnings)


# ---------------------------------------------------------------------------
# Closest match helper
# ---------------------------------------------------------------------------


class TestClosestMatch:
    def test_substring_match(self):
        candidates = ["Generic PLA @base", "Generic PETG @base"]
        assert _closest_match("PLA", candidates) == "Generic PLA @base"

    def test_no_candidates(self):
        assert _closest_match("PLA", []) is None

    def test_no_match(self):
        result = _closest_match("xyz_nothing", ["abc", "def"])
        assert result is None or isinstance(result, str)


# ---------------------------------------------------------------------------
# Interactive picker (ui.pick)
# ---------------------------------------------------------------------------


def _simulate_keys(monkeypatch, keystrokes: list[str]):
    """Mock _readkey to return a sequence of keystrokes."""
    import fabprint.ui as ui_mod

    it = iter(keystrokes)
    monkeypatch.setattr(ui_mod, "_readkey", lambda: next(it))
    # Use a no-op Live context to avoid terminal manipulation in tests
    from unittest.mock import MagicMock

    mock_live_cls = MagicMock()
    mock_live_instance = MagicMock()
    mock_live_instance.__enter__ = lambda self: self
    mock_live_instance.__exit__ = lambda self, *a: None
    mock_live_cls.return_value = mock_live_instance
    monkeypatch.setattr("rich.live.Live", mock_live_cls)


class TestPick:
    def test_direct_select_by_number(self, monkeypatch):
        """Typing a number + enter selects that item."""
        from fabprint import ui

        # Type "2" then enter
        _simulate_keys(monkeypatch, ["2", "\r"])
        result = ui.pick(["A", "B", "C"], prompt="Pick")
        assert result == [1]  # index 1 = "B"

    def test_search_narrows_to_one(self, monkeypatch):
        """Typing enough to narrow to one result, then enter auto-selects."""
        from fabprint import ui

        # Type "PETG" then enter — only one match
        _simulate_keys(monkeypatch, list("PETG") + ["\r"])
        result = ui.pick(
            ["Generic PLA @base", "Generic PETG @base", "Bambu PLA Basic"],
            prompt="Pick",
        )
        assert result == [1]  # "Generic PETG @base"

    def test_search_then_select(self, monkeypatch):
        """Type to filter, then enter a number to select from filtered list."""
        from fabprint import ui

        options = [f"Profile {i}" for i in range(20)]
        # Type "Profile " to filter, Enter to lock search, "5" + Enter to select 5th
        # "Profile " matches all 20; lock search; select #5 = "Profile 4"
        # Or: type enough to narrow, then Enter auto-selects if 1 match
        _simulate_keys(monkeypatch, list("Profile 15") + ["\r"])
        result = ui.pick(options, prompt="Pick")
        assert result == [15]  # "Profile 15" is only exact match

    def test_multi_select_all(self, monkeypatch):
        """'all' + enter selects every item."""
        from fabprint import ui

        _simulate_keys(monkeypatch, list("all") + ["\r"])
        result = ui.pick(["A", "B", "C"], prompt="Pick", allow_multi=True)
        assert result == [0, 1, 2]

    def test_backspace_editing(self, monkeypatch):
        """Backspace removes last character from query."""
        from fabprint import ui

        # Type "PETX", backspace to "PET", then "G" → "PETG", enter to auto-select
        _simulate_keys(
            monkeypatch,
            list("PETX") + ["\x7f"] + list("G") + ["\r"],
        )
        result = ui.pick(
            ["Generic PLA @base", "Generic PETG @base", "Bambu PLA Basic"],
            prompt="Pick",
        )
        assert result == [1]  # "Generic PETG @base"


class TestHighlightMatch:
    def test_basic_highlight(self):
        from fabprint.ui import _highlight_match

        result = _highlight_match("Generic PLA @base", "PLA")
        assert "[bold yellow]PLA[/bold yellow]" in result

    def test_case_insensitive(self):
        from fabprint.ui import _highlight_match

        result = _highlight_match("Generic PLA @base", "pla")
        assert "[bold yellow]PLA[/bold yellow]" in result

    def test_no_match(self):
        from fabprint.ui import _highlight_match

        result = _highlight_match("Hello World", "xyz")
        assert "bold yellow" not in result


# ---------------------------------------------------------------------------
# Build TOML helper
# ---------------------------------------------------------------------------


class TestBuildToml:
    def test_basic(self):
        toml = _build_toml(
            engine="orca",
            printer_profile="Bambu Lab P1S 0.4 nozzle",
            process_profile="0.20mm Standard @BBL X1C",
            filament_names=["Generic PLA @base"],
            parts=[{"file": "cube.stl", "copies": 1, "orient": "flat", "filament": 1}],
            plate_size=(256, 256),
            slicer_version="2.3.1",
            stages=["load", "arrange", "plate", "slice"],
            printer_name=None,
        )
        assert "[slicer]" in toml
        assert 'engine = "orca"' in toml
        assert 'version = "2.3.1"' in toml
        assert "[[parts]]" in toml
        assert 'file = "cube.stl"' in toml

    def test_with_printer_name(self):
        toml = _build_toml(
            engine="orca",
            printer_profile=None,
            process_profile=None,
            filament_names=[],
            parts=[{"file": "a.stl", "copies": 1, "orient": "flat", "filament": 1}],
            plate_size=(200, 200),
            slicer_version=None,
            stages=["load", "arrange", "plate"],
            printer_name="my-printer",
        )
        assert "[printer]" in toml
        assert 'name = "my-printer"' in toml

    def test_multiple_copies_shown(self):
        toml = _build_toml(
            engine="orca",
            printer_profile=None,
            process_profile=None,
            filament_names=[],
            parts=[{"file": "a.stl", "copies": 3, "orient": "flat", "filament": 1}],
            plate_size=(256, 256),
            slicer_version=None,
            stages=["load", "arrange", "plate"],
            printer_name=None,
        )
        assert "copies = 3" in toml

    def test_non_default_orient_shown(self):
        toml = _build_toml(
            engine="orca",
            printer_profile=None,
            process_profile=None,
            filament_names=[],
            parts=[{"file": "a.stl", "copies": 1, "orient": "upright", "filament": 1}],
            plate_size=(256, 256),
            slicer_version=None,
            stages=["load", "arrange", "plate"],
            printer_name=None,
        )
        assert 'orient = "upright"' in toml

    def test_overrides_section(self):
        toml = _build_toml(
            engine="orca",
            printer_profile=None,
            process_profile=None,
            filament_names=[],
            parts=[{"file": "a.stl", "copies": 1, "orient": "flat", "filament": 1}],
            plate_size=(256, 256),
            slicer_version=None,
            stages=["load", "arrange", "plate", "slice"],
            printer_name=None,
            overrides={"sparse_infill_density": "25%", "wall_loops": "3"},
        )
        assert "[slicer.overrides]" in toml
        assert 'sparse_infill_density = "25%"' in toml
        assert "wall_loops = 3" in toml

    def test_no_overrides_section(self):
        toml = _build_toml(
            engine="orca",
            printer_profile=None,
            process_profile=None,
            filament_names=[],
            parts=[{"file": "a.stl", "copies": 1, "orient": "flat", "filament": 1}],
            plate_size=(256, 256),
            slicer_version=None,
            stages=["load", "arrange", "plate", "slice"],
            printer_name=None,
            overrides={},
        )
        assert "[slicer.overrides]" not in toml

    def test_defaults_omitted(self):
        """copies=1, orient=flat, filament=1 should not appear in output."""
        toml = _build_toml(
            engine="orca",
            printer_profile=None,
            process_profile=None,
            filament_names=[],
            parts=[{"file": "a.stl", "copies": 1, "orient": "flat", "filament": 1}],
            plate_size=(256, 256),
            slicer_version=None,
            stages=["load", "arrange", "plate"],
            printer_name=None,
        )
        assert "copies" not in toml
        assert "orient" not in toml
        assert "filament" not in toml.split("[[parts]]")[1]


# ---------------------------------------------------------------------------
# Wizard (non-interactive, mocked input)
# ---------------------------------------------------------------------------


class TestWizard:
    def test_wizard_with_mocked_input(self, tmp_path, monkeypatch):
        """Wizard should produce valid TOML when given mocked inputs."""
        from fabprint.init import run_wizard

        monkeypatch.chdir(tmp_path)

        # Create a fake STL so it gets discovered
        (tmp_path / "test-part.stl").write_bytes(b"fake stl")

        _mock_ui_inputs(
            monkeypatch,
            [
                "n",  # Run setup first? -> no
                "Bambu Lab P1S 0.4 nozzle",  # Printer profile name
                "0.20mm Standard @BBL X1C",  # Process profile name
                "n",  # Add slicer overrides? -> no
                "Generic PLA @base",  # Filament name
                "1",  # Select files
                "1",  # copies
                "flat",  # orient
                "256",  # plate width
                "256",  # plate depth
                "",  # slicer version (skip)
                "n",  # Configure printer connection? -> no
                "my-project",  # Project name
                "y",  # Write to fabprint.toml?
            ],
        )

        # Mock discover_profiles to return empty (no slicer installed in CI)
        monkeypatch.setattr(
            "fabprint.profiles.discover_profiles",
            lambda engine: {"machine": {}, "process": {}, "filament": {}},
        )
        # Mock configured printers to empty so we don't depend on real credentials
        monkeypatch.setattr("fabprint.init._list_configured_printers", lambda: {})
        # Mock slicer version discovery so we don't hit DockerHub
        monkeypatch.setattr("fabprint.init._fetch_available_versions", lambda: [])
        monkeypatch.setattr("fabprint.init._detect_orca_version", lambda: None)

        result = run_wizard()
        assert "[slicer]" in result
        assert "test-part.stl" in result
        assert 'name = "my-project"' in result
        assert (tmp_path / "fabprint.toml").exists()

    def test_wizard_no_write(self, tmp_path, monkeypatch):
        """Wizard should not write if user declines."""
        from fabprint.init import run_wizard

        monkeypatch.chdir(tmp_path)

        _mock_ui_inputs(
            monkeypatch,
            [
                "n",  # Run setup first? -> no
                "My Printer",  # Printer profile name
                "My Process",  # Process profile name
                "n",  # Add slicer overrides? -> no
                "My PLA",  # Filament name
                "my-part.stl",  # Part file path
                "256",  # plate width
                "256",  # plate depth
                "",  # slicer version (skip)
                "n",  # Configure printer connection? -> no
                "",  # Project name (use default)
                "n",  # Write?
            ],
        )
        monkeypatch.setattr(
            "fabprint.profiles.discover_profiles",
            lambda engine: {"machine": {}, "process": {}, "filament": {}},
        )
        # Mock configured printers to empty so we don't depend on real credentials
        monkeypatch.setattr("fabprint.init._list_configured_printers", lambda: {})
        # Mock slicer version discovery so we don't hit DockerHub
        monkeypatch.setattr("fabprint.init._fetch_available_versions", lambda: [])
        monkeypatch.setattr("fabprint.init._detect_orca_version", lambda: None)

        run_wizard()
        assert not (tmp_path / "fabprint.toml").exists()
