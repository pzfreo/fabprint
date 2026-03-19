"""Rich UI helpers for interactive CLI commands."""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.theme import Theme

_THEME = Theme(
    {
        "info": "cyan",
        "success": "green",
        "warning": "yellow",
        "error": "red bold",
        "heading": "bold cyan",
    }
)

console = Console(highlight=False, theme=_THEME)


def heading(text: str) -> None:
    """Print a section heading with a rule line."""
    console.rule(f"[heading]{text}[/heading]", style="dim")


def success(text: str) -> None:
    """Print a success line with green checkmark."""
    console.print(f"  [green]\u2714[/green] {text}")


def warn(text: str) -> None:
    """Print a warning line."""
    console.print(f"  [yellow]\u26a0[/yellow] {text}")


def error(text: str) -> None:
    """Print an error line."""
    console.print(f"  [red]\u2718[/red] {text}")


def info(text: str) -> None:
    """Print an info line."""
    console.print(f"  [dim]{text}[/dim]")


def prompt_str(prompt: str, default: str | None = None) -> str:
    """Prompt for a string value with optional default."""
    result = Prompt.ask(f"  {prompt}", default=default, console=console)
    return result or ""


def prompt_int(prompt: str, default: int) -> int:
    """Prompt for an integer with a default."""
    return IntPrompt.ask(f"  {prompt}", default=default, console=console)


def prompt_yn(prompt: str, default: bool = True) -> bool:
    """Prompt yes/no with a default."""
    return Confirm.ask(f"  {prompt}", default=default, console=console)


def prompt_password(prompt: str) -> str:
    """Prompt for a password (masked input)."""
    return Prompt.ask(f"  {prompt}", password=True, console=console) or ""


def preview_toml(text: str) -> None:
    """Show TOML content with syntax highlighting in a panel."""
    syntax = Syntax(text, "toml", theme="monokai", line_numbers=False)
    console.print(Panel(syntax, title="fabprint.toml", border_style="dim"))


def choice_table(
    items: Sequence[Sequence[str]],
    columns: list[str],
    *,
    markup: bool = False,
) -> None:
    """Print a numbered selection table.

    Set ``markup=True`` to allow Rich markup in cell values (e.g. colors).
    By default all values are escaped to prevent accidental markup injection.
    """
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("#", style="dim", width=4)
    for col in columns:
        table.add_column(col)
    for i, row in enumerate(items, 1):
        cells = row if markup else tuple(escape(c) for c in row)
        table.add_row(str(i), *cells)
    console.print(table)


def color_swatch(hex_color: str) -> str:
    """Return a Rich markup string for a colored swatch block."""
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return f"[on rgb({r},{g},{b})]  [/on rgb({r},{g},{b})]"


# ---------------------------------------------------------------------------
# Interactive picker  (requires Unix terminal — Linux / macOS / WSL)
# ---------------------------------------------------------------------------


def pick(
    options: list[str],
    prompt: str = "Pick",
    allow_multi: bool = False,
) -> list[int]:
    """Interactive picker with type-to-search filtering.

    Uses ``simple-term-menu`` for robust terminal UI.
    Returns a list of indices into the original *options* list.

    Requires a Unix terminal (Linux, macOS, or WSL).
    """
    from simple_term_menu import TerminalMenu

    # For single-select: search_key=None enables type-to-filter (any key searches).
    # For multi-select: keep search_key="/" so Space/Tab work for toggling items.
    search_key: str | None = "/" if allow_multi else None
    search_hint = "(/ to filter, Space to toggle)" if allow_multi else "(type to filter)"

    menu = TerminalMenu(
        options,
        title=f"  {prompt}",
        search_key=search_key,
        multi_select=allow_multi,
        show_multi_select_hint=allow_multi,
        show_search_hint=True,
        show_search_hint_text=search_hint,
    )
    result = menu.show()

    if result is None:
        raise KeyboardInterrupt

    if isinstance(result, tuple):
        selected = list(result)
    else:
        selected = [result]

    for idx in selected:
        success(options[idx])

    return selected
