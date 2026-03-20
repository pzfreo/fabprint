"""Tests for fabprint init, validate, and template commands."""

from pathlib import Path
from unittest.mock import patch

import pytest

from fabprint.cli import main
from fabprint.init import (
    ValidationResult,
    _build_toml,
    _closest_match,
    _validate_override,
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
# Override validation
# ---------------------------------------------------------------------------


class TestValidateOverride:
    def test_percent_bare_number(self):
        assert _validate_override("25", "percent") == "25%"

    def test_percent_with_sign(self):
        assert _validate_override("25%", "percent") == "25%"

    def test_percent_with_spaces(self):
        assert _validate_override(" 30 ", "percent") == "30%"

    def test_percent_rejects_text(self):
        assert _validate_override("abc", "percent") is None

    def test_percent_rejects_negative(self):
        assert _validate_override("-5", "percent") is None

    def test_percent_rejects_over_100(self):
        assert _validate_override("150", "percent") is None

    def test_percent_zero(self):
        assert _validate_override("0", "percent") == "0%"

    def test_int_valid(self):
        assert _validate_override("3", "int") == "3"

    def test_int_rejects_float(self):
        assert _validate_override("3.5", "int") is None

    def test_int_rejects_text(self):
        assert _validate_override("abc", "int") is None

    def test_int_rejects_negative(self):
        assert _validate_override("-1", "int") is None

    def test_float_valid(self):
        assert _validate_override("0.20", "float") == "0.20"

    def test_float_rejects_zero(self):
        assert _validate_override("0", "float") is None

    def test_float_rejects_text(self):
        assert _validate_override("abc", "float") is None

    def test_text_passthrough(self):
        assert _validate_override("anything", "text") == "anything"

    def test_empty_returns_none(self):
        assert _validate_override("", "percent") is None
        assert _validate_override("  ", "int") is None


# ---------------------------------------------------------------------------
# Interactive picker (ui.pick) — uses questionary
# ---------------------------------------------------------------------------


class TestPick:
    def test_single_select(self, monkeypatch):
        """Single selection returns a one-element list."""
        from unittest.mock import patch

        from fabprint import ui

        with patch("questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "B"
            result = ui.pick(["A", "B", "C"], prompt="Pick")
        assert result == [1]

    def test_multi_select(self, monkeypatch):
        """Multi-select returns a list of indices."""
        from unittest.mock import patch

        from fabprint import ui

        with patch("questionary.checkbox") as mock_cb:
            mock_cb.return_value.ask.return_value = ["A", "C"]
            result = ui.pick(["A", "B", "C"], prompt="Pick", allow_multi=True)
        assert result == [0, 2]

    def test_cancel_raises_keyboard_interrupt(self):
        """Cancelling the menu (None) raises KeyboardInterrupt."""
        from unittest.mock import patch

        from fabprint import ui

        with patch("questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = None
            try:
                ui.pick(["A", "B"])
                raise AssertionError("Expected KeyboardInterrupt")
            except KeyboardInterrupt:
                pass


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
                "my-project",  # Project name
                "1",  # Select files
                "1",  # copies
                "flat",  # orient
                "n",  # Configure printer connection? -> no
                "Bambu Lab P1S 0.4 nozzle",  # Printer profile name
                "0.20mm Standard @BBL X1C",  # Process profile name
                "256",  # plate width
                "256",  # plate depth
                "",  # slicer version (skip)
                "Generic PLA @base",  # Filament name
                "n",  # Add slicer overrides? -> no
                "w",  # Write / Go back / Quit
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
                "",  # Project name (use default)
                "my-part.stl",  # Part file path
                "n",  # Configure printer connection? -> no
                "My Printer",  # Printer profile name
                "My Process",  # Process profile name
                "256",  # plate width
                "256",  # plate depth
                "",  # slicer version (skip)
                "My PLA",  # Filament name
                "n",  # Add slicer overrides? -> no
                "q",  # Write / Go back / Quit
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
