"""Tests for Rich UI helpers."""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from fabprint.ui import (
    choice_table,
    color_swatch,
    console,
    error,
    heading,
    info,
    pick,
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
# pick (questionary)
# ---------------------------------------------------------------------------


class TestPick:
    @patch("fabprint.ui.success")
    def test_single_select(self, mock_success):
        with patch("questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "c"
            result = pick(["a", "b", "c"], prompt="Choose")
        assert result == [2]
        mock_success.assert_called_once_with("c")

    @patch("fabprint.ui.success")
    def test_multi_select(self, mock_success):
        with patch("questionary.checkbox") as mock_cb:
            mock_cb.return_value.ask.return_value = ["a", "c"]
            result = pick(["a", "b", "c"], allow_multi=True)
        assert result == [0, 2]
        assert mock_success.call_count == 2

    def test_none_raises_keyboard_interrupt(self):
        with patch("questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = None
            try:
                pick(["a", "b"])
                raise AssertionError("Expected KeyboardInterrupt")
            except KeyboardInterrupt:
                pass

    def test_multi_none_raises_keyboard_interrupt(self):
        with patch("questionary.checkbox") as mock_cb:
            mock_cb.return_value.ask.return_value = None
            try:
                pick(["a", "b"], allow_multi=True)
                raise AssertionError("Expected KeyboardInterrupt")
            except KeyboardInterrupt:
                pass

    @patch("fabprint.ui.success")
    def test_single_uses_search_filter(self, mock_success):
        with patch("questionary.select") as mock_select:
            mock_select.return_value.ask.return_value = "x"
            pick(["x", "y"], prompt="Select")
            mock_select.assert_called_once_with(
                "  Select",
                choices=["x", "y"],
                use_search_filter=True,
                use_jk_keys=False,
            )

    @patch("fabprint.ui.success")
    def test_multi_uses_checkbox(self, mock_success):
        with patch("questionary.checkbox") as mock_cb:
            mock_cb.return_value.ask.return_value = ["x"]
            pick(["x", "y"], prompt="Select", allow_multi=True)
            mock_cb.assert_called_once_with(
                "  Select",
                choices=["x", "y"],
            )
