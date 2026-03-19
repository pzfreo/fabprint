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


# ---------------------------------------------------------------------------
# Interactive picker
# ---------------------------------------------------------------------------

_FILTER_THRESHOLD = 10  # switch to search mode above this many items


def _highlight_match(text: str, query: str) -> str:
    """Return text with the matching substring highlighted in bold yellow."""
    low = text.lower()
    q = query.lower()
    idx = low.find(q)
    if idx == -1:
        return escape(text)
    before = escape(text[:idx])
    match = text[idx : idx + len(query)]
    after = escape(text[idx + len(query) :])
    return f"{before}[bold yellow]{escape(match)}[/bold yellow]{after}"


def _show_options(
    names: list[str],
    query: str | None = None,
) -> None:
    """Display a numbered list of options, highlighting matches if query is set."""
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Name")
    for i, name in enumerate(names, 1):
        label = _highlight_match(name, query) if query else escape(name)
        table.add_row(str(i), label)
    console.print(table)


def pick(
    options: list[str],
    prompt: str = "Pick",
    allow_multi: bool = False,
) -> list[int]:
    """Interactive picker: type to search, enter a number to select.

    For short lists (<=10 items), shows all options immediately.
    For long lists, prompts for a search term first, then shows matches.

    Typing text at any prompt filters the list. Typing a number selects.
    Returns a list of indices into the original ``options`` list.
    """
    filtered = options
    filter_indices = list(range(len(options)))
    query: str | None = None

    # Long list: search first
    if len(options) > _FILTER_THRESHOLD:
        info(f"{len(options)} options available — type to search")
        # Show preview of naming style
        for ex in options[:5]:
            console.print(f"    [dim]{escape(ex)}[/dim]")
        if len(options) > 5:
            console.print(f"    [dim]... and {len(options) - 5} more[/dim]")

        while True:
            query = prompt_str("Search")
            if not query:
                continue
            q = query.lower()
            matches = [(i, o) for i, o in enumerate(options) if q in o.lower()]
            if matches:
                filter_indices = [i for i, _ in matches]
                filtered = [o for _, o in matches]
                info(f"{len(matches)} match(es)")
                break
            warn(f"No matches for '{query}'. Try again.")
    else:
        # Short list: show everything
        pass

    _show_options(filtered, query)

    # Selection loop
    multi_hint = " (comma-separated, 'all', or type to search)" if allow_multi else ""
    while True:
        raw = prompt_str(prompt)
        if not raw:
            continue

        # "all" for multi-select
        if allow_multi and raw.lower() == "all":
            return list(filter_indices)

        # If input starts with a digit, try to parse as selection
        if raw[0].isdigit():
            try:
                if allow_multi:
                    picks = [int(x.strip()) - 1 for x in raw.split(",")]
                else:
                    picks = [int(raw) - 1]
                if all(0 <= p < len(filtered) for p in picks):
                    selected = [filter_indices[p] for p in picks]
                    # Show what was selected
                    for p in picks:
                        success(filtered[p])
                    return selected
            except ValueError:
                pass
            console.print(f"  Enter 1-{len(filtered)}{multi_hint}")
            continue

        # Otherwise treat as a new search
        query = raw
        q = query.lower()
        matches = [(i, o) for i, o in enumerate(options) if q in o.lower()]
        if matches:
            filter_indices = [i for i, _ in matches]
            filtered = [o for _, o in matches]
            info(f"{len(matches)} match(es) for '{query}':")
            _show_options(filtered, query)
        else:
            warn(f"No matches for '{query}'. Try again.")
            # Reset to full list
            filtered = options
            filter_indices = list(range(len(options)))
            query = None
            if len(options) <= _FILTER_THRESHOLD:
                _show_options(filtered)
