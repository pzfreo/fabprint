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
) -> None:
    """Print a numbered selection table."""
    table = Table(show_header=True, header_style="bold", box=None, padding=(0, 2))
    table.add_column("#", style="dim", width=4)
    for col in columns:
        table.add_column(col)
    for i, row in enumerate(items, 1):
        table.add_row(str(i), *[escape(c) for c in row])
    console.print(table)


def color_swatch(hex_color: str) -> str:
    """Return a Rich markup string for a colored swatch block."""
    r = int(hex_color[0:2], 16)
    g = int(hex_color[2:4], 16)
    b = int(hex_color[4:6], 16)
    return f"[on rgb({r},{g},{b})]  [/on rgb({r},{g},{b})]"
