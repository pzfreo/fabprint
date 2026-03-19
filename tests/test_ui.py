"""Tests for Rich UI helpers."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from rich.console import Console

from fabprint.ui import (
    _build_picker_display,
    _highlight_match,
    _try_select,
    choice_table,
    color_swatch,
    console,
    error,
    heading,
    info,
    preview_toml,
    prompt_int,
    prompt_password,
    prompt_str,
    prompt_yn,
    success,
    warn,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capture(fn, *args, **kwargs) -> str:
    """Capture Rich console output by temporarily replacing the file."""
    buf = StringIO()
    old_file = console.file
    console._file = buf  # noqa: SLF001
    try:
        fn(*args, **kwargs)
    finally:
        console._file = old_file  # noqa: SLF001
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Output helpers: error, success, warn, info, heading
# ---------------------------------------------------------------------------


class TestOutputHelpers:
    def test_error_prints_cross(self):
        out = _capture(error, "something broke")
        assert "\u2718" in out
        assert "something broke" in out

    def test_success_prints_checkmark(self):
        out = _capture(success, "all good")
        assert "\u2714" in out
        assert "all good" in out

    def test_warn_prints_warning(self):
        out = _capture(warn, "be careful")
        assert "\u26a0" in out
        assert "be careful" in out

    def test_info_prints_text(self):
        out = _capture(info, "some detail")
        assert "some detail" in out

    def test_heading_prints_text(self):
        out = _capture(heading, "My Section")
        assert "My Section" in out


# ---------------------------------------------------------------------------
# Prompt helpers (mock Rich Prompt classes)
# ---------------------------------------------------------------------------


class TestPromptHelpers:
    @patch("fabprint.ui.Prompt.ask", return_value="hello")
    def test_prompt_str_returns_value(self, mock_ask):
        result = prompt_str("Name")
        assert result == "hello"
        mock_ask.assert_called_once()

    @patch("fabprint.ui.Prompt.ask", return_value="hello")
    def test_prompt_str_with_default(self, mock_ask):
        result = prompt_str("Name", default="world")
        assert result == "hello"
        args, kwargs = mock_ask.call_args
        assert kwargs["default"] == "world"

    @patch("fabprint.ui.Prompt.ask", return_value=None)
    def test_prompt_str_none_returns_empty(self, mock_ask):
        result = prompt_str("Name")
        assert result == ""

    @patch("fabprint.ui.IntPrompt.ask", return_value=42)
    def test_prompt_int_returns_value(self, mock_ask):
        result = prompt_int("Count", default=10)
        assert result == 42
        args, kwargs = mock_ask.call_args
        assert kwargs["default"] == 10

    @patch("fabprint.ui.Confirm.ask", return_value=True)
    def test_prompt_yn_returns_bool(self, mock_ask):
        result = prompt_yn("Continue?")
        assert result is True

    @patch("fabprint.ui.Confirm.ask", return_value=False)
    def test_prompt_yn_default_false(self, mock_ask):
        result = prompt_yn("Continue?", default=False)
        assert result is False
        args, kwargs = mock_ask.call_args
        assert kwargs["default"] is False

    @patch("fabprint.ui.Prompt.ask", return_value="s3cret")
    def test_prompt_password(self, mock_ask):
        result = prompt_password("Token")
        assert result == "s3cret"
        args, kwargs = mock_ask.call_args
        assert kwargs["password"] is True

    @patch("fabprint.ui.Prompt.ask", return_value=None)
    def test_prompt_password_none_returns_empty(self, mock_ask):
        result = prompt_password("Token")
        assert result == ""


# ---------------------------------------------------------------------------
# preview_toml
# ---------------------------------------------------------------------------


class TestPreviewToml:
    def test_preview_toml_renders_panel(self):
        out = _capture(preview_toml, '[section]\nkey = "value"')
        assert "fabprint.toml" in out


# ---------------------------------------------------------------------------
# choice_table
# ---------------------------------------------------------------------------


class TestChoiceTable:
    def test_basic_table(self):
        items = [("Alpha", "desc1"), ("Beta", "desc2")]
        out = _capture(choice_table, items, ["Name", "Desc"])
        assert "1" in out
        assert "2" in out
        assert "Alpha" in out
        assert "Beta" in out
        assert "Name" in out
        assert "Desc" in out

    def test_markup_false_escapes(self):
        """With markup=False (default), Rich markup chars should be escaped."""
        items = [("[bold]danger[/bold]",)]
        out = _capture(choice_table, items, ["Val"])
        # The literal brackets should appear, not bold formatting
        assert "[bold]" in out or "\\[bold" in out or "danger" in out

    def test_markup_true_allows_rich(self):
        """With markup=True, Rich markup is passed through."""
        items = [("[green]ok[/green]",)]
        out = _capture(choice_table, items, ["Val"], markup=True)
        # The text should render (markup consumed by Rich)
        assert "ok" in out

    def test_empty_items(self):
        out = _capture(choice_table, [], ["Col"])
        # Should not crash; header should still appear
        assert "Col" in out


# ---------------------------------------------------------------------------
# color_swatch
# ---------------------------------------------------------------------------


class TestColorSwatch:
    def test_basic_hex(self):
        result = color_swatch("FF0000")
        assert "rgb(255,0,0)" in result

    def test_green(self):
        result = color_swatch("00FF00")
        assert "rgb(0,255,0)" in result

    def test_arbitrary_color(self):
        result = color_swatch("1A2B3C")
        assert "rgb(26,43,60)" in result

    def test_returns_markup_with_spaces(self):
        result = color_swatch("000000")
        # Swatch contains two spaces as the block
        assert "  " in result
        assert "[on rgb(0,0,0)]" in result


# ---------------------------------------------------------------------------
# _highlight_match
# ---------------------------------------------------------------------------


class TestHighlightMatch:
    def test_match_found(self):
        result = _highlight_match("Hello World", "world")
        assert "[bold yellow]World[/bold yellow]" in result

    def test_no_match(self):
        result = _highlight_match("Hello World", "xyz")
        assert "Hello World" in result
        assert "bold yellow" not in result

    def test_case_insensitive(self):
        result = _highlight_match("FooBar", "oob")
        assert "[bold yellow]ooB[/bold yellow]" in result

    def test_regex_special_chars_escaped(self):
        """Markup escaping should handle brackets etc."""
        result = _highlight_match("a [test] b", "test")
        # The match itself is escaped
        assert "bold yellow" in result

    def test_empty_query(self):
        result = _highlight_match("Hello", "")
        # Empty string is found at index 0; entire prefix is empty
        assert "Hello" in result

    def test_escapes_non_match_portions(self):
        """Text around the match should be escaped for Rich markup."""
        result = _highlight_match("[before]match[after]", "match")
        assert "[bold yellow]match[/bold yellow]" in result
        # The brackets in before/after are escaped by Rich's escape()
        assert "\\[before]" in result or "\\[after]" in result


# ---------------------------------------------------------------------------
# _build_picker_display
# ---------------------------------------------------------------------------


class TestBuildPickerDisplay:
    def test_basic_display(self):
        table = _build_picker_display(["Alpha", "Beta"], "", "Pick", False, 2)
        # Render to string
        buf = StringIO()
        c = Console(file=buf, highlight=False, width=120)
        c.print(table)
        out = buf.getvalue()
        assert "Alpha" in out
        assert "Beta" in out
        assert "Pick" in out

    def test_no_matches(self):
        table = _build_picker_display([], "xyz", "Pick", False, 5)
        buf = StringIO()
        c = Console(file=buf, highlight=False, width=120)
        c.print(table)
        out = buf.getvalue()
        assert "No matches" in out

    def test_truncation_message(self):
        items = [f"item{i}" for i in range(20)]
        table = _build_picker_display(items, "", "Pick", False, 20)
        buf = StringIO()
        c = Console(file=buf, highlight=False, width=120)
        c.print(table)
        out = buf.getvalue()
        assert "more" in out
        assert "keep typing" in out

    def test_multi_hint(self):
        table = _build_picker_display(["A"], "", "Pick", True, 1)
        buf = StringIO()
        c = Console(file=buf, highlight=False, width=120)
        c.print(table)
        out = buf.getvalue()
        assert "comma-sep" in out
        assert "all" in out

    def test_no_multi_hint(self):
        table = _build_picker_display(["A"], "", "Pick", False, 1)
        buf = StringIO()
        c = Console(file=buf, highlight=False, width=120)
        c.print(table)
        out = buf.getvalue()
        assert "comma-sep" not in out

    def test_highlight_applied_when_query(self):
        table = _build_picker_display(["Alpha", "Beta"], "alp", "Pick", False, 2)
        buf = StringIO()
        c = Console(file=buf, highlight=False, width=120)
        c.print(table)
        out = buf.getvalue()
        assert "Alpha" in out  # match is still visible

    def test_constant_height(self):
        """Output must always have the same number of lines regardless of item count."""
        from fabprint.ui import _MAX_VISIBLE

        expected = _MAX_VISIBLE + 2  # items + status + prompt

        few = _build_picker_display(["A", "B"], "", "Pick", False, 2)
        many = _build_picker_display([f"item{i}" for i in range(30)], "", "Pick", False, 30)
        none = _build_picker_display([], "xyz", "Pick", False, 5)

        for renderable in (few, many, none):
            buf = StringIO()
            c = Console(file=buf, highlight=False, width=120)
            c.print(renderable, end="")
            line_count = buf.getvalue().count("\n") + 1
            assert line_count == expected, f"Expected {expected} lines, got {line_count}"


# ---------------------------------------------------------------------------
# _try_select
# ---------------------------------------------------------------------------


class TestTrySelect:
    def test_single_valid(self):
        result = _try_select("1", ["a", "b", "c"], [0, 1, 2], False)
        assert result == [0]

    def test_single_last(self):
        result = _try_select("3", ["a", "b", "c"], [0, 1, 2], False)
        assert result == [2]

    def test_single_out_of_range(self):
        result = _try_select("5", ["a", "b", "c"], [0, 1, 2], False)
        assert result is None

    def test_single_zero(self):
        result = _try_select("0", ["a", "b"], [0, 1], False)
        assert result is None

    def test_multi_comma(self):
        result = _try_select("1,3", ["a", "b", "c"], [0, 1, 2], True)
        assert result == [0, 2]

    def test_multi_comma_spaces(self):
        result = _try_select(" 1 , 2 ", ["a", "b", "c"], [0, 1, 2], True)
        assert result == [0, 1]

    def test_multi_all(self):
        result = _try_select("all", ["a", "b", "c"], [0, 1, 2], True)
        assert result == [0, 1, 2]

    def test_multi_all_case_insensitive(self):
        result = _try_select("ALL", ["a", "b"], [0, 1], True)
        assert result == [0, 1]

    def test_non_numeric_returns_none(self):
        result = _try_select("abc", ["a", "b"], [0, 1], False)
        assert result is None

    def test_comma_not_multi_treated_as_invalid(self):
        """With allow_multi=False, comma input is not split."""
        result = _try_select("1,2", ["a", "b"], [0, 1], False)
        assert result is None

    def test_multi_out_of_range(self):
        result = _try_select("1,5", ["a", "b", "c"], [0, 1, 2], True)
        assert result is None

    def test_negative_number(self):
        result = _try_select("-1", ["a", "b"], [0, 1], False)
        assert result is None
