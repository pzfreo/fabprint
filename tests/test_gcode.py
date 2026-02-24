"""Tests for shared gcode metadata parsing."""

from fabprint.gcode import parse_gcode_metadata


def test_parse_print_time(tmp_path):
    gcode = tmp_path / "test.gcode"
    gcode.write_text("; total estimated time: 1h 7m 32s\nG28\n")
    stats = parse_gcode_metadata(gcode)
    assert stats["print_time"] == "1h 7m 32s"
    assert stats["print_time_secs"] == 3600 + 7 * 60 + 32


def test_parse_filament_weight(tmp_path):
    # Filament stats appear in the last 50 lines
    lines = ["G1 X0\n"] * 10
    lines.append("; total filament used [g] = 12.34\n")
    lines.append("; total filament used [cm3] = 9.87\n")
    gcode = tmp_path / "test.gcode"
    gcode.write_text("".join(lines))
    stats = parse_gcode_metadata(gcode)
    assert stats["filament_g"] == 12.34
    assert stats["filament_cm3"] == 9.87


def test_parse_empty_gcode(tmp_path):
    gcode = tmp_path / "empty.gcode"
    gcode.write_text("G28\nG1 X0\n")
    stats = parse_gcode_metadata(gcode)
    assert "print_time" not in stats
    assert "filament_g" not in stats


def test_parse_alternative_time_format(tmp_path):
    gcode = tmp_path / "test.gcode"
    gcode.write_text("; estimated printing time (normal mode) = 2h 30m 0s\nG28\n")
    stats = parse_gcode_metadata(gcode)
    assert stats["print_time"] == "2h 30m 0s"
    assert stats["print_time_secs"] == 2 * 3600 + 30 * 60


def test_parse_minutes_only(tmp_path):
    gcode = tmp_path / "test.gcode"
    gcode.write_text("; total estimated time: 45m 10s\nG28\n")
    stats = parse_gcode_metadata(gcode)
    assert stats["print_time_secs"] == 45 * 60 + 10
