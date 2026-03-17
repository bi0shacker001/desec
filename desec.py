#!/usr/bin/env python3
"""
desec.py — Entry point for the deSEC DNS manager.

This is the ONLY script in the root of this repository.  It is the single
entry point for all three interfaces:
  - TUI (Textual terminal interface) — default when no subcommand is given
  - GUI (PySide6/Qt native interface) — launched with --gui
  - CLI (command-line scripting)      — launched with a subcommand

USAGE:
  desec                          Launch the interactive TUI
  desec --gui                    Launch the Qt GUI
  desec token list               List all API tokens (CLI)
  desec domain list              List all domains (CLI)
  desec record list DOMAIN       List DNS records for a domain (CLI)
  desec ddns-add DOMAIN [opts]   Provision a DDNS token
  desec cert-add DOMAIN [opts]   Provision a cert token (single domain)
  desec cert-multi --entry ...   Provision a cert token (multi-domain)
  desec --help                   Show full help

CONFIG:
  ~/.config/mech-goodies/desec.env   Stores DESEC_TOKEN and DESEC_API_BASE

DEPENDENCIES:
  textual   — terminal UI framework (pip install textual)
  httpx     — HTTP client (pip install httpx)
  pyyaml    — YAML output support, optional (pip install pyyaml)
  PySide6   — Qt GUI, optional (pip install PySide6)

TROUBLESHOOTING:
  - "No API token found" → set DESEC_TOKEN in ~/.config/mech-goodies/desec.env
    or pass --token YOUR_TOKEN on the command line
  - TUI won't start → pip install textual
  - GUI won't start → pip install PySide6
  - API returns 401 → token is wrong or expired; generate a new one at desec.io
  - API returns 403 → token lacks required permission (perm_manage_tokens needed for most operations)
"""
from __future__ import annotations

# ── Standard library imports ──────────────────────────────────────────────────
import argparse   # command-line argument parsing (built into Python)
import os         # for reading environment variables
import sys        # for sys.exit() and sys.stderr
from pathlib import Path  # for resolving this script's directory

# ── Path setup ────────────────────────────────────────────────────────────────
# Ensure the directory containing this script (programs/desec/) is in sys.path.
# This allows `from core.env import ...` and `from tui.app import ...` to work
# whether you run `python desec.py` directly, `./desec.py`, or via an installed
# package entry point.
#
# Without this: running `python /some/path/desec.py` from a different working
# directory would fail with "ModuleNotFoundError: No module named 'core'"
# because Python adds the working directory (not the script's directory) to path.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ── Optional YAML support ─────────────────────────────────────────────────────
# pyyaml is not required — we fall back to JSON if it's missing.
try:
    import yaml as _yaml
    _YAML_AVAILABLE = True
except ImportError:
    _yaml = None            # type: ignore[assignment]
    _YAML_AVAILABLE = False

# ── Core modules ──────────────────────────────────────────────────────────────
# These are always safe to import — they have no heavy dependencies.
from core.env import load_env, ensure_env_complete
from core.api import (
    list_tokens, create_token, delete_token,
    list_policies, create_policy,
    list_domains, create_domain, delete_domain,
    list_rrsets, create_rrset, update_rrset, delete_rrset,
    provision_ddns_token, provision_cert_token, provision_cert_multi_token,
    _acme_subname,
)
from core.output import _print_output, _require_token

import httpx   # HTTP client — needed for catching HTTPStatusError in CLI handlers


# ──────────────────────────────────────────────────────────────────────────────
# CLI argument parser
# ──────────────────────────────────────────────────────────────────────────────

def _build_cli_parser() -> argparse.ArgumentParser:
    """
    Build and return the argument parser for the CLI.

    The parser covers:
      - Top-level flags: --token, --output, --ui, --gui
      - Subcommands: token, domain, record, ddns-add, cert-add, cert-multi
      - Sub-sub-commands: token list/create/delete, domain list/create/delete, etc.

    Returns:
      argparse.ArgumentParser — the fully configured parser

    The parser is built here and returned so it can be reused in _run_cli()
    for printing help messages on invalid subcommand combinations.
    """
    env = load_env()

    p = argparse.ArgumentParser(
        prog="desec",
        description="deSEC Token & DNS Manager — TUI, GUI, and CLI",
        epilog=(
            "Config: ~/.config/mech-goodies/desec.env  |  "
            "Deps: pip install textual httpx  |  "
            "DESEC_TOKEN env var is also accepted."
        ),
    )

    # Global flags that apply to all subcommands
    p.add_argument(
        "--token",
        default=os.environ.get("DESEC_TOKEN") or env.get("DESEC_TOKEN", ""),
        help="deSEC API token (overrides DESEC_TOKEN env var and desec.env)",
    )
    p.add_argument(
        "--output", "-o",
        choices=["table", "json", "yaml"],
        default="table",
        help="Output format: table (default), json, or yaml",
    )
    p.add_argument(
        "--ui",
        action="store_true",
        help="Launch the interactive TUI (default when no subcommand given)",
    )
    p.add_argument(
        "--gui",
        action="store_true",
        help="Launch the Qt/PySide6 native GUI",
    )

    sub = p.add_subparsers(dest="command", metavar="COMMAND")

    # ── token subcommand ──────────────────────────────────────────────────────
    # Manages API tokens on the deSEC account
    tp = sub.add_parser("token", help="Manage API tokens")
    tsub = tp.add_subparsers(dest="token_cmd", metavar="ACTION")

    tsub.add_parser("list", help="List all tokens")

    tc = tsub.add_parser("create", help="Create a new token")
    tc.add_argument("name", help="Token name (human-readable label)")
    tc.add_argument("--perm-manage-tokens", action="store_true",
                    help="Allow this token to create/delete other tokens")
    tc.add_argument("--perm-create-domain",  action="store_true",
                    help="Allow this token to register new domains")
    tc.add_argument("--perm-delete-domain",  action="store_true",
                    help="Allow this token to delete domains")
    tc.add_argument("--auto-policy",         action="store_true",
                    help="Auto-create permissive policy when a domain is created with this token")
    tc.add_argument("--subnets",
                    help="Comma-separated CIDR ranges allowed to use this token (blank = any IP)")
    tc.add_argument("--max-unused",
                    help="Auto-revoke token after this ISO 8601 duration of non-use (e.g. P90D)")

    td = tsub.add_parser("delete", help="Delete a token by ID")
    td.add_argument("token_id", help="Token UUID (from 'desec token list')")

    # ── domain subcommand ─────────────────────────────────────────────────────
    # Manages DNS domains registered with deSEC
    dp = sub.add_parser("domain", help="Manage domains")
    dsub = dp.add_subparsers(dest="domain_cmd", metavar="ACTION")

    dsub.add_parser("list", help="List all registered domains")

    dca = dsub.add_parser("create", help="Register a new domain")
    dca.add_argument("name", help="Full domain name (e.g. myhost.dedyn.io)")

    dde = dsub.add_parser("delete", help="Delete a domain and ALL its DNS records")
    dde.add_argument("name", help="Domain name to delete")
    dde.add_argument("--yes", "-y", action="store_true",
                     help="Skip the interactive confirmation prompt (for scripts)")

    # ── record subcommand ─────────────────────────────────────────────────────
    # Manages DNS records (RRsets) within a domain
    rp = sub.add_parser("record", help="Manage DNS records (RRsets)")
    rsub = rp.add_subparsers(dest="record_cmd", metavar="ACTION")

    rl = rsub.add_parser("list", help="List all DNS records for a domain")
    rl.add_argument("domain", help="Domain name")

    ra = rsub.add_parser("add", help="Create a new DNS record set")
    ra.add_argument("domain", help="Domain name")
    ra.add_argument("--subname", default="",
                    help="Subdomain label (blank or '@' = domain apex)")
    ra.add_argument("--type", dest="rtype", required=True, metavar="TYPE",
                    help="DNS record type: A, AAAA, CNAME, MX, TXT, etc.")
    ra.add_argument("--ttl", type=int, default=3600,
                    help="Time-to-live in seconds (default: 3600 = 1 hour)")
    ra.add_argument("--rdata", action="append", required=True, dest="rdata", metavar="VALUE",
                    help="Record value in RDATA format; repeat for multiple values "
                         "(e.g. --rdata 1.2.3.4)")

    re_ = rsub.add_parser("edit", help="Replace all records in an existing RRset")
    re_.add_argument("domain")
    re_.add_argument("--subname", default="")
    re_.add_argument("--type", dest="rtype", required=True, metavar="TYPE")
    re_.add_argument("--ttl", type=int, default=3600)
    re_.add_argument("--rdata", action="append", required=True, dest="rdata", metavar="VALUE")

    rdel = rsub.add_parser("delete", help="Delete an entire RRset (all records of that type)")
    rdel.add_argument("domain")
    rdel.add_argument("--subname", default="")
    rdel.add_argument("--type", dest="rtype", required=True, metavar="TYPE")
    rdel.add_argument("--yes", "-y", action="store_true",
                      help="Skip confirmation prompt")

    # ── ddns-add subcommand ───────────────────────────────────────────────────
    # Provisions a DDNS-scoped token (A+AAAA write for one hostname)
    da = sub.add_parser(
        "ddns-add",
        help="Provision a DDNS token: sets initial address records + creates A/AAAA-only scoped key",
    )
    da.add_argument("domain", help="Domain name (e.g. myhost.dedyn.io)")
    da.add_argument("--subname", default="",
                    help="Subdomain label (blank = apex @)")
    da.add_argument("--ipv4", metavar="ADDR",
                    help="Initial IPv4 address — sets A record now (optional)")
    da.add_argument("--ipv6", metavar="ADDR",
                    help="Initial IPv6 address — sets AAAA record now (optional)")
    da.add_argument("--ttl", type=int, default=3600,
                    help="TTL for initial records (default: 3600)")
    da.add_argument("--token-name", dest="token_name", metavar="NAME",
                    help="Name for the new token (default: <hostname>-ddns)")

    # ── cert-add subcommand ───────────────────────────────────────────────────
    # Provisions a single-domain cert token (TXT write at _acme-challenge only)
    ca = sub.add_parser(
        "cert-add",
        help="Provision a cert token: optional initial records + TXT-only scoped key for DNS-01",
    )
    ca.add_argument("domain", help="Domain name")
    ca.add_argument("--subname", default="",
                    help="Subdomain label of the host to certify (blank = apex)")
    ca.add_argument("--ipv4", metavar="ADDR",
                    help="Set initial A record now (not granted to token)")
    ca.add_argument("--ipv6", metavar="ADDR",
                    help="Set initial AAAA record now (not granted to token)")
    ca.add_argument("--cname", metavar="TARGET",
                    help="Set initial CNAME now (mutually exclusive with --ipv4/--ipv6)")
    ca.add_argument("--ttl", type=int, default=3600)
    ca.add_argument("--token-name", dest="token_name", metavar="NAME",
                    help="Name for the new token (default: <hostname>-cert)")

    # ── cert-multi subcommand ─────────────────────────────────────────────────
    # Provisions a multi-domain cert token (TXT write at _acme-challenge for each hostname)
    cm = sub.add_parser(
        "cert-multi",
        help="Provision a multi-domain cert token: TXT-only for multiple hostnames",
    )
    cm.add_argument(
        "--entry", action="append", metavar="DOMAIN[:SUBNAME]", dest="entries",
        required=True,
        help="Add one hostname entry; repeat for each hostname "
             "(e.g. --entry example.dedyn.io --entry example.dedyn.io:www)",
    )
    cm.add_argument("--token-name", dest="token_name", metavar="NAME", required=True,
                    help="Name for the new token")

    return p


# ──────────────────────────────────────────────────────────────────────────────
# CLI handler
# ──────────────────────────────────────────────────────────────────────────────

def _run_cli(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    """
    Execute the requested CLI subcommand.

    All HTTP calls are made synchronously here (blocking).  This is fine for
    CLI use — we don't need async because the user waits for output anyway.

    Parameters:
      args   — parsed argparse namespace from parser.parse_args()
      parser — the parser (used to call parse_args([...,"--help"]) for help output)

    Raises:
      httpx.HTTPStatusError — caught by the caller (main block at bottom of file)
      Exception             — caught by the caller
    """
    # Resolve the API token from args, env var, or config file
    tok = _require_token(args, parser)
    fmt = args.output   # "table", "json", or "yaml"

    # ── token ─────────────────────────────────────────────────────────────────
    if args.command == "token":
        if not args.token_cmd or args.token_cmd == "list":
            # List all tokens: fetch and display in the requested format
            tokens = list_tokens(tok)
            _print_output(
                tokens, fmt,
                headers=["Name", "ID", "Manage?", "Create?", "Delete?", "Subnets"],
                rows=[
                    [
                        t.get("name") or "",
                        t.get("id", ""),
                        "yes" if t.get("perm_manage_tokens") else "no",
                        "yes" if t.get("perm_create_domain")  else "no",
                        "yes" if t.get("perm_delete_domain")  else "no",
                        ", ".join(t.get("allowed_subnets") or []) or "any",
                    ]
                    for t in tokens
                ],
            )

        elif args.token_cmd == "create":
            # Parse subnets: split the comma-separated string, drop empties
            subnets = [s.strip() for s in (args.subnets or "").split(",") if s.strip()]
            result = create_token(
                tok, args.name,
                perm_manage_tokens=args.perm_manage_tokens,
                perm_create_domain=args.perm_create_domain,
                perm_delete_domain=args.perm_delete_domain,
                allowed_subnets=subnets,
                max_unused_period=args.max_unused or None,
                auto_policy=args.auto_policy,
            )
            if fmt in ("json", "yaml"):
                _print_output(result, fmt)
            else:
                # For table output, print a human-friendly summary with the secret
                secret = result.get("token", "(not returned)")
                print(f"Token created:  {result.get('name')}  ({result.get('id')})")
                print(f"Secret (copy now — shown once):  {secret}")

        elif args.token_cmd == "delete":
            delete_token(tok, args.token_id)
            print(f"Deleted token {args.token_id}")

        else:
            # Unknown action — show subcommand help
            parser.parse_args(["token", "--help"])

    # ── domain ────────────────────────────────────────────────────────────────
    elif args.command == "domain":
        if not args.domain_cmd or args.domain_cmd == "list":
            domains = list_domains(tok)
            _print_output(
                domains, fmt,
                headers=["Name", "Created", "Min TTL"],
                rows=[
                    [
                        d.get("name", ""),
                        (d.get("created") or "")[:10],   # trim to date portion only
                        str(d.get("minimum_ttl", "")),
                    ]
                    for d in domains
                ],
            )

        elif args.domain_cmd == "create":
            result = create_domain(tok, args.name)
            if fmt in ("json", "yaml"):
                _print_output(result, fmt)
            else:
                print(f"Domain registered: {result.get('name')}")

        elif args.domain_cmd == "delete":
            # Require explicit confirmation for destructive operations unless --yes was passed
            if not args.yes:
                confirm = input(f"Delete domain '{args.name}' and ALL its records? [y/N] ")
                if confirm.lower() not in ("y", "yes"):
                    print("Aborted.")
                    return
            delete_domain(tok, args.name)
            print(f"Deleted domain {args.name}")

        else:
            parser.parse_args(["domain", "--help"])

    # ── record ────────────────────────────────────────────────────────────────
    elif args.command == "record":
        if not args.record_cmd or args.record_cmd == "list":
            rrsets = list_rrsets(tok, args.domain)
            _print_output(
                rrsets, fmt,
                headers=["Subname", "Type", "TTL", "Records"],
                rows=[
                    [
                        rr.get("subname") or "@",   # show "@" for apex records
                        rr.get("type", ""),
                        str(rr.get("ttl", "")),
                        "; ".join(rr.get("records", [])),  # join multiple values with ";"
                    ]
                    for rr in rrsets
                ],
            )

        elif args.record_cmd == "add":
            result = create_rrset(tok, args.domain, args.subname,
                                   args.rtype, args.ttl, args.rdata)
            if fmt in ("json", "yaml"):
                _print_output(result, fmt)
            else:
                sub = result.get("subname") or "@"
                print(f"Created {sub} {result.get('type')} (TTL {result.get('ttl')})")

        elif args.record_cmd == "edit":
            result = update_rrset(tok, args.domain, args.subname,
                                   args.rtype, args.ttl, args.rdata)
            if fmt in ("json", "yaml"):
                _print_output(result, fmt)
            else:
                sub = result.get("subname") or "@"
                print(f"Updated {sub} {result.get('type')} (TTL {result.get('ttl')})")

        elif args.record_cmd == "delete":
            # Confirm before deleting unless --yes was passed
            if not args.yes:
                sub = args.subname or "@"
                confirm = input(f"Delete {sub} {args.rtype} from '{args.domain}'? [y/N] ")
                if confirm.lower() not in ("y", "yes"):
                    print("Aborted.")
                    return
            delete_rrset(tok, args.domain, args.subname, args.rtype)
            sub = args.subname or "@"
            print(f"Deleted {sub} {args.rtype} from {args.domain}")

        else:
            parser.parse_args(["record", "--help"])

    # ── ddns-add ──────────────────────────────────────────────────────────────
    elif args.command == "ddns-add":
        # Build the display hostname for output
        host = f"{args.subname}.{args.domain}" if args.subname else args.domain
        name = args.token_name or f"{host}-ddns"   # auto-generate name if not given
        result = provision_ddns_token(
            tok, name, args.domain, args.subname,
            args.ipv4 or None, args.ipv6 or None, args.ttl,
        )
        if fmt in ("json", "yaml"):
            _print_output(result, fmt)
        else:
            secret = result.get("token", "(not returned)")
            print(f"DDNS token created: {result.get('name')}  ({result.get('id')})")
            print(f"Hostname:           {host}")
            print(f"Permitted records:  A, AAAA  (write-only for {host})")
            print(f"Secret (copy now):  {secret}")

    # ── cert-add ──────────────────────────────────────────────────────────────
    elif args.command == "cert-add":
        # CNAME and A/AAAA are mutually exclusive — catch this before hitting the API
        if args.cname and (args.ipv4 or args.ipv6):
            parser.error("--cname is mutually exclusive with --ipv4 / --ipv6")
        host = f"{args.subname}.{args.domain}" if args.subname else args.domain
        name = args.token_name or f"{host}-cert"
        result = provision_cert_token(
            tok, name, args.domain, args.subname,
            args.ipv4 or None, args.ipv6 or None, args.cname or None, args.ttl,
        )
        # Show the ACME challenge FQDN (where the cert client needs to write)
        acme = f"{_acme_subname(args.subname)}.{args.domain}."
        if fmt in ("json", "yaml"):
            _print_output(result, fmt)
        else:
            secret = result.get("token", "(not returned)")
            print(f"Cert token created: {result.get('name')}  ({result.get('id')})")
            print(f"Hostname:           {host}")
            print(f"ACME challenge at:  {acme}  (TXT write-only)")
            print(f"Secret (copy now):  {secret}")

    # ── cert-multi ────────────────────────────────────────────────────────────
    elif args.command == "cert-multi":
        # Parse each --entry value into a (domain, subname) tuple
        # Format: "domain" or "domain:subname"
        entries: list[tuple[str, str]] = []
        for raw in args.entries:
            if ":" in raw:
                domain, subname = raw.split(":", 1)
            else:
                domain, subname = raw, ""
            entries.append((domain.strip(), subname.strip()))

        result = provision_cert_multi_token(tok, args.token_name, entries)
        if fmt in ("json", "yaml"):
            _print_output(result, fmt)
        else:
            secret = result.get("token", "(not returned)")
            print(f"Multi-cert token: {result.get('name')}  ({result.get('id')})")
            for domain, subname in entries:
                acme = f"{_acme_subname(subname)}.{domain}."
                print(f"  ACME challenge:  {acme}  (TXT write-only)")
            print(f"Secret (copy now): {secret}")

    else:
        # No recognized subcommand — print help
        parser.print_help()


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Build the argument parser and parse command-line arguments
    _parser = _build_cli_parser()
    _args = _parser.parse_args()

    # ── GUI mode ───────────────────────────────────────────────────────────────
    if _args.gui:
        # Launch the Qt/PySide6 GUI
        # Import is done here (not at the top) so the CLI works even without PySide6 installed
        from gui.app import main as _gui_main
        _gui_main()

    # ── TUI mode (default) ────────────────────────────────────────────────────
    elif _args.ui or _args.command is None:
        # No subcommand given, or --ui flag passed → launch the Textual TUI
        try:
            import textual  # noqa: F401 — just checking it's installed
        except ImportError:
            print(
                "Textual is required for the TUI.  Install it with:\n"
                "  pip install textual httpx\n\n"
                "Or use the CLI directly with a subcommand (e.g. 'desec token list').",
                file=sys.stderr,
            )
            sys.exit(1)

        from tui.app import DeSECApp
        DeSECApp().run()

    # ── CLI mode ───────────────────────────────────────────────────────────────
    else:
        # A subcommand was given → run it as a CLI command
        try:
            _run_cli(_args, _parser)
        except httpx.HTTPStatusError as _e:
            # HTTP error from the deSEC API — print status code and error body
            print(
                f"API error {_e.response.status_code}: {_e.response.text[:300]}",
                file=sys.stderr,
            )
            sys.exit(1)
        except Exception as _e:
            # Any other unexpected error
            print(f"Error: {_e}", file=sys.stderr)
            sys.exit(1)
