"""Rich UI helpers for interactive CLI commands."""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
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
# Interactive picker
# ---------------------------------------------------------------------------

_MAX_VISIBLE = 15  # max rows shown in the live picker


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


def _build_picker_display(
    filtered: list[str],
    query: str,
    prompt: str,
    allow_multi: bool,
    total: int,
) -> Text:
    """Build the Rich renderable for the live picker.

    Always produces exactly ``_MAX_VISIBLE + 2`` lines so that Rich Live's
    cursor-up count is constant across refreshes.  Variable height causes
    Rich to miscalculate how many lines to overwrite, resulting in duplicate
    or side-by-side rendering.
    """
    lines: list[str] = []

    # Options list — one line per item
    visible = filtered[:_MAX_VISIBLE]
    for i, name in enumerate(visible, 1):
        label = _highlight_match(name, query) if query else escape(name)
        lines.append(f"  [dim]{i:>4}[/dim]  {label}")

    # Pad to fixed height so Rich Live cursor math is always correct
    while len(lines) < _MAX_VISIBLE:
        lines.append("")

    # Status line (always exactly 1 line)
    if len(filtered) > _MAX_VISIBLE:
        remaining = len(filtered) - _MAX_VISIBLE
        lines.append(f"  [dim]... and {remaining} more (keep typing to narrow)[/dim]")
    elif not filtered:
        lines.append("  [dim]No matches — keep typing or backspace[/dim]")
    else:
        lines.append("")

    # Prompt line (always exactly 1 line)
    multi_hint = " [dim](comma-sep, 'all')[/dim]" if allow_multi else ""
    lines.append(f"  [bold]{prompt}>[/bold] {escape(query)}[blink]▌[/blink]{multi_hint}")

    return Text.from_markup("\n".join(lines))


def _readkey() -> str:
    """Read a single keypress, cross-platform.

    Note: reads 1 byte, so multi-byte UTF-8 chars (accented names etc.)
    are not supported. All current option lists are ASCII.
    """
    import sys

    if sys.platform == "win32":
        import msvcrt

        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            msvcrt.getwch()  # consume second byte of special key
            return ""
        return ch
    else:
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            # Handle escape sequences (arrow keys etc)
            if ch == "\x1b":
                import select

                if select.select([sys.stdin], [], [], 0.05)[0]:
                    sys.stdin.read(1)  # [
                    if select.select([sys.stdin], [], [], 0.05)[0]:
                        sys.stdin.read(1)  # A/B/C/D
                return ""
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


def pick(
    options: list[str],
    prompt: str = "Pick",
    allow_multi: bool = False,
) -> list[int]:
    """Interactive picker with live search filtering.

    Type letters to filter the list in real-time. Type a number to select.
    Backspace to edit. Enter to confirm when one match remains or a number
    is entered.
    Returns a list of indices into the original ``options`` list.
    """
    from rich.live import Live

    search = ""  # text filter
    sel_buf = ""  # numeric selection buffer
    sel: list[int] = []  # resolved selection indices (set before break)
    search_locked = False  # True after user presses Enter to lock search
    filtered = list(options)
    filter_indices = list(range(len(options)))

    info(f"{len(options)} options — type to search, enter number to select")

    def _display_query() -> str:
        return search + ((" → " + sel_buf) if sel_buf else "")

    with Live(
        _build_picker_display(filtered, _display_query(), prompt, allow_multi, len(options)),
        console=console,
        refresh_per_second=15,
        transient=True,
    ) as live:
        while True:
            ch = _readkey()

            if not ch:
                continue

            # Ctrl-C / Ctrl-D → abort
            if ch in ("\x03", "\x04"):
                raise KeyboardInterrupt

            # Backspace
            if ch in ("\x7f", "\x08"):
                if sel_buf:
                    sel_buf = sel_buf[:-1]
                elif search:
                    search = search[:-1]
            # Enter
            elif ch in ("\r", "\n"):
                # If exactly one match, auto-select it
                if len(filtered) == 1:
                    break
                # "all" for multi-select → select everything
                if allow_multi and search.strip().lower() == "all":
                    sel = list(range(len(options)))
                    filtered = options
                    filter_indices = list(range(len(options)))
                    break
                # Try selection buffer as number
                if sel_buf.strip():
                    maybe = _try_select(sel_buf, filtered, filter_indices, allow_multi)
                    if maybe is not None:
                        sel = maybe
                        break
                # Enter with search results: lock search, switch to selection
                if search and not search_locked and len(filtered) > 0:
                    search_locked = True
                    live.update(
                        _build_picker_display(
                            filtered,
                            _display_query(),
                            prompt,
                            allow_multi,
                            len(options),
                        )
                    )
                    continue
                live.update(
                    _build_picker_display(
                        filtered,
                        _display_query(),
                        prompt,
                        allow_multi,
                        len(options),
                    )
                )
                continue
            # Regular character
            elif ch.isprintable():
                if search_locked:
                    # After search is locked, everything goes to selection
                    sel_buf += ch
                elif search or not ch.isdigit():
                    search += ch
                    sel_buf = ""
                else:
                    sel_buf += ch
            else:
                continue

            # Re-filter by search text
            if search:
                q = search.lower()
                matches = [(i, o) for i, o in enumerate(options) if q in o.lower()]
                filter_indices = [i for i, _ in matches]
                filtered = [o for _, o in matches]
            else:
                filtered = list(options)
                filter_indices = list(range(len(options)))

            live.update(
                _build_picker_display(
                    filtered,
                    _display_query(),
                    prompt,
                    allow_multi,
                    len(options),
                )
            )

    # Show what was selected
    if len(filtered) == 1:
        success(filtered[0])
        return [filter_indices[0]]

    for p in sel:
        success(filtered[p])
    return [filter_indices[p] for p in sel]


def _try_select(
    query: str,
    filtered: list[str],
    filter_indices: list[int],
    allow_multi: bool,
) -> list[int] | None:
    """Try to parse query as a numeric selection. Returns pick indices or None."""
    try:
        if allow_multi and "," in query:
            picks = [int(x.strip()) - 1 for x in query.split(",")]
        elif allow_multi and query.strip().lower() == "all":
            return list(range(len(filtered)))
        else:
            picks = [int(query.strip()) - 1]
        if all(0 <= p < len(filtered) for p in picks):
            return picks
    except ValueError:
        pass
    return None
