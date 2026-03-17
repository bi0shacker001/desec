# core/env.py
#
# Configuration file management for the desec program.
#
# deSEC credentials are stored in a plain-text .env file at:
#   ~/.config/mech-goodies/desec.env
#
# The file format is simple KEY=VALUE, one per line, with comment lines
# starting with #.  Commented-out lines look like:  # KEY=default_value
# This lets us keep placeholder stubs for every setting so the user can
# see what's available even if they haven't filled everything in yet.
#
# HOW THIS FILE FITS INTO THE PROGRAM:
#   - desec.py calls load_env() and ensure_env_complete() on startup
#   - LoginScreen (tui/screens/login.py) calls save_env() after a successful login
#   - api.py calls load_env() to read DESEC_API_BASE at call time
#
# IF SOMETHING IS BROKEN HERE:
#   - "Token not found" errors → check that DESEC_TOKEN= (no # prefix) is in desec.env
#   - File not created → verify ~/.config/mech-goodies/ directory is writable
#   - Wrong API URL → check DESEC_API_BASE= in desec.env (default: https://desec.io/api/v1)

from __future__ import annotations

import re                  # used to parse individual lines of the .env file
from pathlib import Path   # cross-platform file path handling


# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────

# Full path to the config file.  Path.home() resolves to /home/<user> on Linux
# or /Users/<user> on macOS.  We keep all mech-goodies configs together under
# ~/.config/mech-goodies/ so they're easy to find and back up.
ENV_PATH = Path.home() / ".config" / "mech-goodies" / "desec.env"


# ENV_SCHEMA defines every variable this program understands.
#
# Each entry is a dict with:
#   key     — the environment variable name (must be UPPER_CASE)
#   default — the placeholder value written when the key is first stubs out
#   secret  — True means this value should be stored as a real (active) value
#             when first saved, not as a commented-out stub.  False means start
#             as a comment so the user explicitly edits it.
#   comment — human-readable explanation written as a # line above the key
#
# To add a new setting: add an entry here.  The rest of the code picks it up
# automatically (save_env writes stubs for missing keys, ensure_env_complete
# adds any stubs missing from an existing file).
ENV_SCHEMA: list[dict] = [
    {
        "key": "DESEC_TOKEN",
        "default": "",
        "secret": True,
        # perm_manage_tokens is required because this token manages other tokens.
        # A read-only token will get 403 errors on most operations.
        "comment": "deSEC master API token (must have perm_manage_tokens)",
    },
    {
        "key": "DESEC_API_BASE",
        "default": "https://desec.io/api/v1",
        "secret": False,
        # Only change this if you are running a self-hosted deSEC instance.
        # The public deSEC service is always at https://desec.io/api/v1
        "comment": "deSEC API base URL (change only for self-hosted instances)",
    },
]


# ──────────────────────────────────────────────────────────────────────────────
# Internal parser
# ──────────────────────────────────────────────────────────────────────────────

def _parse_env_file(path: Path) -> dict[str, str | None]:
    """
    Read a .env file and return a dict of {KEY: value_or_None}.

    Return value meanings:
      KEY present with a value   → {KEY: "the_value"}
      KEY present but commented  → {KEY: None}
      KEY not in file at all     → key absent from dict entirely

    We distinguish "commented out" from "absent" so that save_env() can tell
    whether a key exists as a stub (and needs to be activated) vs. needs to be
    appended at the bottom.

    Parameters:
      path  — the Path object pointing at the .env file to read

    This function does NOT raise if the file doesn't exist — it just returns {}.
    """
    result: dict[str, str | None] = {}

    # If the file hasn't been created yet (first run), return empty dict.
    # The caller (load_env / save_env) handles the "no file" case gracefully.
    if not path.exists():
        return result

    for line in path.read_text().splitlines():
        # Check for a commented-out variable: lines like  # DESEC_TOKEN=
        # These are placeholder stubs the user hasn't filled in yet.
        m = re.match(r"^#\s*([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if m:
            # Record as None to indicate "present but not active"
            result[m.group(1)] = None
            continue

        # Check for an active variable: lines like  DESEC_TOKEN=abc123
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if m:
            # Strip surrounding whitespace and any surrounding quotes the user
            # may have added (both single and double quotes are accepted).
            result[m.group(1)] = m.group(2).strip().strip('"').strip("'")

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────────────────────

def load_env() -> dict[str, str]:
    """
    Return a dict of all active (non-commented) values from the env file.

    Example return value:
      {"DESEC_TOKEN": "abc123", "DESEC_API_BASE": "https://desec.io/api/v1"}

    Commented-out keys and missing keys are both excluded from the result.
    If the file doesn't exist yet, returns an empty dict (no crash).
    """
    parsed = _parse_env_file(ENV_PATH)
    # Filter out keys that are None (commented-out stubs) — only keep real values
    return {k: v for k, v in parsed.items() if v is not None}


def save_env(updates: dict[str, str]) -> None:
    """
    Write or update the env file with the given key=value pairs.

    Behaviour:
      - Keys in `updates`: written as active  KEY=value  lines (replacing any
        existing active or commented-out line for that key).
      - Schema keys completely absent from the file: appended as commented stubs
        so the user can see what settings exist.
      - Existing lines not in `updates` are preserved unchanged (order kept).
      - The directory is created if it doesn't exist.

    Parameters:
      updates — dict of KEY: value pairs to write/activate

    Use this after a successful login to persist the API token:
      save_env({"DESEC_TOKEN": "the_token_value"})
    """
    # Create ~/.config/mech-goodies/ if it doesn't exist yet
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Read the current file content so we can update in-place
    existing_lines: list[str] = []
    if ENV_PATH.exists():
        existing_lines = ENV_PATH.read_text().splitlines()

    # Also parse the file so we know which keys are present (active or stub)
    parsed = _parse_env_file(ENV_PATH)

    new_lines: list[str] = []
    replaced: set[str] = set()  # tracks which update keys we've already handled

    # Walk every existing line and replace any line matching a key in `updates`
    for line in existing_lines:
        matched_key: str | None = None

        # Does this line contain an active key we need to update?
        m = re.match(r"^([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if m and m.group(1) in updates:
            matched_key = m.group(1)

        # Does this line contain a COMMENTED key we need to activate?
        m2 = re.match(r"^#\s*([A-Z_][A-Z0-9_]*)=(.*)$", line)
        if m2 and m2.group(1) in updates:
            matched_key = m2.group(1)

        if matched_key:
            # Write the new active KEY=value (replacing the old line)
            new_lines.append(f"{matched_key}={updates[matched_key]}")
            replaced.add(matched_key)
        else:
            # Not a line we care about — keep it exactly as-is
            new_lines.append(line)

    # If any update keys weren't found in the existing file, append them now
    for key, value in updates.items():
        if key not in replaced:
            # Try to find a matching schema entry so we can write its comment
            schema_entry = next((s for s in ENV_SCHEMA if s["key"] == key), None)
            if schema_entry:
                new_lines.append(f"\n# {schema_entry['comment']}")
            new_lines.append(f"{key}={value}")
            replaced.add(key)

    # Append stubs for any schema keys that aren't in the file at all yet.
    # This ensures new settings added to ENV_SCHEMA appear in the user's file
    # as commented-out placeholders they can fill in.
    for entry in ENV_SCHEMA:
        key = entry["key"]
        if key not in parsed and key not in replaced:
            new_lines.append(f"\n# {entry['comment']}")
            new_lines.append(f"# {key}={entry['default']}")

    # Write the updated content back.  "\n".join() + trailing "\n" = valid Unix file.
    ENV_PATH.write_text("\n".join(new_lines) + "\n")


def ensure_env_complete() -> None:
    """
    Check the existing env file and append stubs for any schema keys that are missing.

    Call this on startup after the file already exists (e.g. the user installed a
    new version with new settings).  If the file doesn't exist yet, this is a no-op —
    we create it only after the first successful login (in save_env).

    This is safe to call every startup because it only appends; it never overwrites.
    """
    # Nothing to do if the file hasn't been created yet
    if not ENV_PATH.exists():
        return

    parsed = _parse_env_file(ENV_PATH)

    # Find any schema entries that are completely absent (neither active nor stub)
    missing = [e for e in ENV_SCHEMA if e["key"] not in parsed]
    if not missing:
        return  # All known keys are already present — nothing to do

    # Append the missing keys as commented-out stubs at the end of the file
    with ENV_PATH.open("a") as f:
        for entry in missing:
            f.write(f"\n# {entry['comment']}\n")
            f.write(f"# {entry['key']}={entry['default']}\n")
