# core/output.py
#
# CLI output helpers and token resolution for the desec program.
#
# This module is only used by the CLI path (desec.py _run_cli).
# The TUI never calls anything here — it renders data directly in Textual widgets.
#
# WHAT'S IN HERE:
#   _print_table()  — render a list of rows as a formatted table on stdout
#   _print_output() — unified dispatcher: table / JSON / YAML based on --output flag
#   _require_token()— resolve the API token from args/env, or exit with a clear error
#
# IF OUTPUT LOOKS WRONG:
#   - Garbled characters → your terminal may not support Unicode; try --output json
#   - No Rich formatting → `pip install rich` for coloured tables; falls back to plain text
#   - "No API token found" → see _require_token() docstring for resolution steps

from __future__ import annotations

import json             # built-in JSON encoder; used for --output json
import os               # used to read the DESEC_TOKEN environment variable
import argparse         # needed for type hint on parser parameter
from typing import Any  # for flexible type annotations

# Try to import yaml for --output yaml support.
# yaml is an optional dependency — if not installed, we fall back to JSON.
try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _yaml = None        # type: ignore[assignment]
    _YAML_AVAILABLE = False

from .env import load_env   # reads the desec.env config file


# ──────────────────────────────────────────────────────────────────────────────
# Table rendering
# ──────────────────────────────────────────────────────────────────────────────

def _print_table(headers: list[str], rows: list[list[str]]) -> None:
    """
    Print a formatted table to stdout.

    Tries to use the `rich` library for nice coloured output.  If rich is not
    installed, falls back to a plain-text fixed-width table.

    Parameters:
      headers — list of column header strings, e.g. ["Name", "ID", "Created"]
      rows    — list of row data.  Each row is a list of string values,
                one per column, in the same order as headers.

    Example:
      _print_table(["Name", "ID"], [["my-token", "abc123"], ["other", "def456"]])
    """
    try:
        # rich.table produces nicely formatted coloured output in supported terminals
        from rich.table import Table
        from rich.console import Console
        t = Table(*headers, highlight=True)
        for r in rows:
            t.add_row(*r)
        Console().print(t)
    except ImportError:
        # rich is not installed — fall back to a plain fixed-width text table
        # Calculate the width needed for each column
        widths = [
            max(len(h), max((len(str(r[i])) for r in rows), default=0))
            for i, h in enumerate(headers)
        ]
        # Build a format string like "{:<10}  {:<20}  {:<8}"
        fmt = "  ".join(f"{{:<{w}}}" for w in widths)
        print(fmt.format(*headers))
        print("  ".join("-" * w for w in widths))
        for r in rows:
            print(fmt.format(*[str(c) for c in r]))


def _print_output(
    data: Any,
    fmt: str,
    headers: list[str] | None = None,
    rows: list[list[str]] | None = None,
) -> None:
    """
    Print data in the requested output format: table, json, or yaml.

    Parameters:
      data    — the raw Python object (list/dict) to serialise for json/yaml output
      fmt     — one of "table", "json", "yaml"
      headers — column headers for table output (required if fmt=="table")
      rows    — table rows for table output (required if fmt=="table")

    Behaviour by format:
      "yaml"  → serialize `data` as YAML.  Falls back to JSON if pyyaml not installed.
      "json"  → serialize `data` as pretty-printed JSON (2-space indent).
      "table" → call _print_table(headers, rows) if both are provided;
                otherwise fall back to JSON (avoids crashes on missing table data).
    """
    if fmt == "yaml":
        if _yaml is not None:
            # dump() produces YAML text.  rstrip() removes the trailing newline
            # that yaml.dump always adds, so our print() adds exactly one.
            print(_yaml.dump(
                data,
                default_flow_style=False,   # use block style (more readable)
                allow_unicode=True,          # don't escape non-ASCII chars
                sort_keys=False,             # preserve insertion order
            ).rstrip())
        else:
            # yaml not installed — fall back to JSON which is always available
            print(json.dumps(data, indent=2, ensure_ascii=False))

    elif fmt == "json":
        # ensure_ascii=False keeps non-ASCII domain names readable
        print(json.dumps(data, indent=2, ensure_ascii=False))

    else:
        # Table format (the default)
        if headers is not None and rows is not None:
            _print_table(headers, rows)
        else:
            # headers/rows not provided — fall back to JSON to avoid crashing
            print(json.dumps(data, indent=2, ensure_ascii=False))


# ──────────────────────────────────────────────────────────────────────────────
# Token resolution
# ──────────────────────────────────────────────────────────────────────────────

def _require_token(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    """
    Resolve the deSEC API token from the available sources and return it.

    Token lookup precedence (highest to lowest priority):
      1. --token flag passed on the command line
      2. DESEC_TOKEN environment variable
      3. DESEC_TOKEN= in ~/.config/mech-goodies/desec.env

    If no token is found from any source, prints a helpful error message and
    calls sys.exit(2) via parser.error() — this exits the program cleanly.

    Parameters:
      args   — parsed argparse namespace (from argparse.ArgumentParser.parse_args())
      parser — the argument parser, used to call parser.error() for clean exit

    Returns:
      str — the resolved API token string

    Example error message (when no token found):
      "No API token found. Pass --token, set DESEC_TOKEN env var,
       or add DESEC_TOKEN to ~/.config/mech-goodies/desec.env"
    """
    # Check each source in priority order
    tok = (
        args.token                                  # --token flag
        or os.environ.get("DESEC_TOKEN", "")        # DESEC_TOKEN env var
        or load_env().get("DESEC_TOKEN", "")        # desec.env file
    )
    if not tok:
        # parser.error() prints "error: <message>" and exits with code 2
        parser.error(
            "No API token found. Pass --token, set DESEC_TOKEN env var, "
            "or add DESEC_TOKEN to ~/.config/mech-goodies/desec.env"
        )
    return tok
