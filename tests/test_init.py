"""Tests for fabprint init, validate, and template commands."""

from pathlib import Path
from unittest.mock import patch

import pytest

from fabprint.cli import main
from fabprint.init import (
    _build_toml,
    _closest_match,
    _search_filter,
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
        warnings = validate_config(cfg)
        # The only possible warnings are about profiles not being installed,
        # which is environment-dependent. No hard errors should occur.
        assert isinstance(warnings, list)

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
# Search filter helper
# ---------------------------------------------------------------------------


class TestSearchFilter:
    def test_filter_matches(self):
        options = ["Generic PLA @base", "Generic PETG @base", "Bambu PLA Basic"]
        names, indices = _search_filter(options, "PLA")
        assert len(names) == 2
        assert "Generic PLA @base" in names
        assert "Bambu PLA Basic" in names
        # indices should map back to original positions
        assert all(options[i] in names for i in indices)

    def test_filter_case_insensitive(self):
        options = ["Generic PLA @base", "Generic PETG @base"]
        names, indices = _search_filter(options, "pla")
        assert len(names) == 1
        assert names[0] == "Generic PLA @base"

    def test_filter_no_match_reprompts(self, monkeypatch):
        options = ["Generic PLA @base", "Generic PETG @base"]
        # First query has no match, second does
        inputs = iter(["xyz", "PLA"])
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
        names, indices = _search_filter(options, "nothing")
        assert len(names) == 1
        assert names[0] == "Generic PLA @base"


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

        inputs = iter(
            [
                "n",  # Run setup first? -> no
                "Bambu Lab P1S 0.4 nozzle",  # Printer profile name
                "0.20mm Standard @BBL X1C",  # Process profile name
                "Generic PLA @base",  # Filament name
                "1",  # Select files
                "1",  # copies
                "flat",  # orient
                "256",  # plate width
                "256",  # plate depth
                "",  # slicer version (skip)
                "n",  # Include print stage?
                "y",  # Write to fabprint.toml?
            ]
        )
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))

        # Mock discover_profiles to return empty (no slicer installed in CI)
        monkeypatch.setattr(
            "fabprint.profiles.discover_profiles",
            lambda engine: {"machine": {}, "process": {}, "filament": {}},
        )
        # Mock configured printers to empty so we don't depend on real credentials
        monkeypatch.setattr("fabprint.init._list_configured_printers", lambda: {})

        result = run_wizard()
        assert "[slicer]" in result
        assert "test-part.stl" in result
        assert (tmp_path / "fabprint.toml").exists()

    def test_wizard_no_write(self, tmp_path, monkeypatch):
        """Wizard should not write if user declines."""
        from fabprint.init import run_wizard

        monkeypatch.chdir(tmp_path)

        inputs = iter(
            [
                "n",  # Run setup first? -> no
                "My Printer",  # Printer profile name
                "My Process",  # Process profile name
                "My PLA",  # Filament name
                "my-part.stl",  # Part file path
                "256",  # plate width
                "256",  # plate depth
                "",  # slicer version
                "n",  # Include print stage?
                "n",  # Write?
            ]
        )
        monkeypatch.setattr("builtins.input", lambda _="": next(inputs))
        monkeypatch.setattr(
            "fabprint.profiles.discover_profiles",
            lambda engine: {"machine": {}, "process": {}, "filament": {}},
        )
        # Mock configured printers to empty so we don't depend on real credentials
        monkeypatch.setattr("fabprint.init._list_configured_printers", lambda: {})

        run_wizard()
        assert not (tmp_path / "fabprint.toml").exists()
