# tui/screens/provision.py
#
# Provisioning wizard screens for the desec TUI.
#
# SCREENS IN THIS FILE:
#   DdnsAddModal     — wizard: provision a DDNS token for dynamic IP updates
#   CertAddModal     — wizard: provision a single-domain cert token
#   CertMultiScreen  — wizard: provision a multi-domain cert token
#
# WHAT "PROVISIONING" MEANS:
#   These wizards do several API calls in one step:
#     1. Optionally set initial DNS records (A/AAAA/CNAME)
#     2. Create a new API token with MINIMAL permissions
#     3. Add just the right policies so the token can only do what it needs
#
#   The result is a "scoped" API token you give to a specific service.  That
#   service can update its own DNS records but nothing else.
#
# DDNS TOKEN:
#   Grants write access to A + AAAA records for ONE hostname.
#   Use case: home router with a changing public IP, or any dynamic-IP host.
#   Give this token to ddclient, inadyn, or a cron script.
#
# CERT TOKEN (single-domain):
#   Grants write access to ONE _acme-challenge TXT record.
#   Use case: auto-renewing a TLS certificate with Let's Encrypt DNS-01 challenge.
#   Give this token to certbot --dns-desec or acme.sh --dns desec.
#
# CERT TOKEN (multi-domain):
#   Like the single-domain cert token but covers multiple hostnames in one token.
#   Use case: a wildcard or SAN certificate covering several subdomains.

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Select, Static
from textual import on, work

from core.api import provision_ddns_token, provision_cert_token, provision_cert_multi_token
from core.api import _acme_subname
from tui.widgets import MessageModal, NewSecretModal

if TYPE_CHECKING:
    from tui.app import DeSECApp


# ──────────────────────────────────────────────────────────────────────────────
# DdnsAddModal
# ──────────────────────────────────────────────────────────────────────────────

class DdnsAddModal(ModalScreen):
    """
    Wizard modal for provisioning a DDNS-scoped API token.

    Collects:
      - Domain (from user's registered domains)
      - Subdomain label (blank = apex)
      - Optional initial IPv4 address (sets A record now)
      - Optional initial IPv6 address (sets AAAA record now)
      - Token name (auto-generated from hostname if left blank)

    Returns a dict with the collected values, or None if cancelled.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, domains: list[str]):
        """
        Parameters:
          domains — list of domain names the user has registered with deSEC
        """
        super().__init__()
        self._domains = sorted(domains)   # sort for consistent display order

    def compose(self) -> ComposeResult:
        """Build the DDNS wizard form."""
        domain_opts = [(d, d) for d in self._domains]
        # Default selection is the first domain in the sorted list
        default_domain = self._domains[0] if self._domains else ""

        with Container(id="modal-box", classes="wide"):
            yield Label("[bold cyan]Provision DDNS Token[/]", id="modal-title")
            with ScrollableContainer():
                yield Static(
                    "[dim]Creates A + AAAA write access for one hostname. "
                    "The generated token cannot touch any other record, type, or domain.[/]",
                    id="modal-body",
                )
                yield Label("Domain")
                yield Select(options=domain_opts, id="ddns-domain", value=default_domain)

                yield Label("Subdomain label  [dim](blank = apex @)[/]")
                yield Input(placeholder="home  or  server  or  <blank>", id="ddns-subname")

                yield Label("Initial IPv4  [dim](optional — sets A record now)[/]")
                yield Input(placeholder="1.2.3.4", id="ddns-ipv4")

                yield Label("Initial IPv6  [dim](optional — sets AAAA record now)[/]")
                yield Input(placeholder="2001:db8::1", id="ddns-ipv6")

                yield Label("Token name")
                yield Input(placeholder="my-server-ddns", id="ddns-tokname")

            with Horizontal(id="modal-buttons"):
                yield Button("Create", id="create-btn", variant="success")
                yield Button("Cancel", id="cancel-btn")

    def on_mount(self) -> None:
        """Focus the subname field for quick entry."""
        self.query_one("#ddns-subname", Input).focus()

    @on(Button.Pressed, "#create-btn")
    def do_create(self) -> None:
        """Validate form and dismiss with the collected values."""
        domain  = str(self.query_one("#ddns-domain", Select).value)
        subname = self.query_one("#ddns-subname", Input).value.strip()
        ipv4    = self.query_one("#ddns-ipv4",    Input).value.strip() or None
        ipv6    = self.query_one("#ddns-ipv6",    Input).value.strip() or None
        name    = self.query_one("#ddns-tokname", Input).value.strip()

        if not domain:
            self.app.push_screen(MessageModal("Error", "Domain is required.", is_error=True))
            return

        # Auto-generate a token name if the user left it blank
        if not name:
            host = f"{subname}.{domain}" if subname else domain
            name = f"{host}-ddns"

        self.dismiss({
            "domain": domain,
            "subname": subname,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "name": name,
        })

    @on(Button.Pressed, "#cancel-btn")
    def action_cancel(self) -> None:
        """User cancelled — dismiss with None."""
        self.dismiss(None)


# ──────────────────────────────────────────────────────────────────────────────
# CertAddModal
# ──────────────────────────────────────────────────────────────────────────────

class CertAddModal(ModalScreen):
    """
    Wizard modal for provisioning a single-domain cert-scoped API token.

    Collects:
      - Domain and subdomain to certify
      - Optional initial A/AAAA/CNAME records (set now using master token)
      - Token name

    The generated token gets write access ONLY to the _acme-challenge TXT record
    at the specified hostname.  It cannot touch address records or other domains.

    Returns a dict with the collected values, or None if cancelled.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, domains: list[str]):
        """
        Parameters:
          domains — list of registered domain names for the dropdown
        """
        super().__init__()
        self._domains = sorted(domains)

    def compose(self) -> ComposeResult:
        """Build the single-cert wizard form."""
        domain_opts = [(d, d) for d in self._domains]
        default_domain = self._domains[0] if self._domains else ""

        with Container(id="modal-box", classes="wide"):
            yield Label("[bold cyan]Provision Single-Domain Cert Token[/]", id="modal-title")
            with ScrollableContainer():
                yield Static(
                    "[dim]Optionally sets initial address/CNAME records (using your master token), "
                    "then creates a token scoped only to TXT writes at "
                    "[bold]_acme-challenge.<hostname>[/] — DNS-01 ACME challenges only.[/]",
                    id="modal-body",
                )
                yield Label("Domain")
                yield Select(options=domain_opts, id="cert-domain", value=default_domain)

                yield Label("Subdomain label  [dim](blank = apex @)[/]")
                yield Input(placeholder="www  or  api  or  <blank>", id="cert-subname")

                yield Label("Initial IPv4  [dim](sets A record now — not granted to token)[/]")
                yield Input(placeholder="1.2.3.4", id="cert-ipv4")

                yield Label("Initial IPv6  [dim](sets AAAA record now — not granted to token)[/]")
                yield Input(placeholder="2001:db8::1", id="cert-ipv6")

                yield Label("Initial CNAME  [dim](alternative to A/AAAA — not granted to token)[/]")
                yield Input(placeholder="target.example.com.", id="cert-cname")

                yield Label("Token name")
                yield Input(placeholder="my-server-cert", id="cert-tokname")

            with Horizontal(id="modal-buttons"):
                yield Button("Create", id="create-btn", variant="success")
                yield Button("Cancel", id="cancel-btn")

    def on_mount(self) -> None:
        """Focus the subname field for quick entry."""
        self.query_one("#cert-subname", Input).focus()

    @on(Button.Pressed, "#create-btn")
    def do_create(self) -> None:
        """Validate form, check mutual exclusion, and dismiss with values."""
        domain  = str(self.query_one("#cert-domain", Select).value)
        subname = self.query_one("#cert-subname", Input).value.strip()
        ipv4    = self.query_one("#cert-ipv4",   Input).value.strip() or None
        ipv6    = self.query_one("#cert-ipv6",   Input).value.strip() or None
        cname   = self.query_one("#cert-cname",  Input).value.strip() or None
        name    = self.query_one("#cert-tokname",Input).value.strip()

        if not domain:
            self.app.push_screen(MessageModal("Error", "Domain is required.", is_error=True))
            return

        # CNAME and address records are mutually exclusive in DNS
        if cname and (ipv4 or ipv6):
            self.app.push_screen(MessageModal(
                "Error",
                "Specify either CNAME or address records — not both.",
                is_error=True,
            ))
            return

        # Auto-generate token name from hostname
        if not name:
            host = f"{subname}.{domain}" if subname else domain
            name = f"{host}-cert"

        self.dismiss({
            "domain": domain,
            "subname": subname,
            "ipv4": ipv4,
            "ipv6": ipv6,
            "cname": cname,
            "name": name,
        })

    @on(Button.Pressed, "#cancel-btn")
    def action_cancel(self) -> None:
        """User cancelled — dismiss with None."""
        self.dismiss(None)


# ──────────────────────────────────────────────────────────────────────────────
# CertMultiScreen
# ──────────────────────────────────────────────────────────────────────────────

class CertMultiScreen(Screen):
    """
    Full-screen wizard for provisioning a multi-domain cert-scoped token.

    The user builds a list of (domain, subname) entries interactively.
    Each entry will grant TXT write access at _acme-challenge.<hostname>.
    No address records are touched or granted — strict least-privilege.

    When satisfied with the list, the user names the token and clicks Create.
    The resulting token is shown in a NewSecretModal.
    """

    if TYPE_CHECKING:
        @property
        def app(self) -> DeSECApp: ...   # type: ignore[override]

    BINDINGS = [
        Binding("escape,b", "go_back",   "Cancel"),
        Binding("a",        "add_entry", "Add"),
        Binding("d",        "del_entry", "Remove"),
    ]

    def __init__(self, domains: list[str]):
        """
        Parameters:
          domains — list of registered domain names for the dropdown
        """
        super().__init__()
        self._domains = sorted(domains)
        # The list of (domain, subname) tuples the user has added so far
        self._entries: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        """Build the multi-domain cert wizard layout."""
        domain_opts = [(d, d) for d in self._domains]
        default_domain = self._domains[0] if self._domains else ""

        yield Header()
        with Vertical(id="certmulti-screen"):
            yield Label("[bold cyan]Provision Multi-Domain Cert Token[/]", id="certmulti-title")
            yield Static(
                "[dim]Grants TXT write access at [bold]_acme-challenge.<hostname>[/] for each "
                "entry.  No address records are touched or granted — strict least-privilege.[/]"
            )

            # Table showing the entries the user has added
            yield DataTable(id="certmulti-table", cursor_type="row")

            # Add-entry row: domain dropdown + subname input + Add button
            with Horizontal(id="certmulti-add-row"):
                yield Select(options=domain_opts, id="cm-domain", value=default_domain)
                yield Input(placeholder="subname (blank = apex)", id="cm-subname")
                yield Button("Add (a)", id="add-btn", variant="success")

            yield Label("Token name")
            yield Input(placeholder="multi-cert", id="certmulti-name")

            with Horizontal(id="certmulti-actions"):
                yield Button("Remove Selected (d)", id="del-btn",    variant="error")
                yield Button("Create Token",        id="create-btn", variant="primary")
                yield Button("Cancel (Esc)",        id="cancel-btn")

        yield Footer()

    def on_mount(self) -> None:
        """Set up the entry table columns."""
        self.query_one("#certmulti-table", DataTable).add_columns(
            "Domain", "Subname", "ACME Challenge Subname"
        )

    def action_go_back(self) -> None:
        """Cancel — return to the previous screen without creating a token."""
        self.app.pop_screen()

    def action_add_entry(self) -> None:
        """
        Add the current domain + subname selection to the entry list.
        Validates that a domain is selected and that the entry isn't a duplicate.
        """
        domain  = str(self.query_one("#cm-domain",  Select).value)
        subname = self.query_one("#cm-subname", Input).value.strip()

        if not domain:
            self.app.push_screen(MessageModal("Error", "Domain is required.", is_error=True))
            return

        entry = (domain, subname)
        if entry in self._entries:
            # Don't allow the same (domain, subname) pair twice
            self.app.push_screen(MessageModal(
                "Duplicate",
                f"{domain} ({subname or '@'}) is already in the list.",
            ))
            return

        self._entries.append(entry)
        # Compute the actual _acme-challenge subname to show in the table
        acme = _acme_subname(subname)
        self.query_one("#certmulti-table", DataTable).add_row(domain, subname or "@", acme)

    def action_del_entry(self) -> None:
        """Remove the currently highlighted entry from the list and redraw the table."""
        t = self.query_one("#certmulti-table", DataTable)
        if not self._entries or t.cursor_row < 0 or t.cursor_row >= len(self._entries):
            return

        # Remove the entry from our list
        self._entries.pop(t.cursor_row)

        # Redraw the table from scratch (DataTable doesn't support row removal by index)
        t.clear()
        for domain, subname in self._entries:
            t.add_row(domain, subname or "@", _acme_subname(subname))

    @on(Button.Pressed, "#add-btn")
    def on_add(self) -> None: self.action_add_entry()

    @on(Button.Pressed, "#del-btn")
    def on_del(self) -> None: self.action_del_entry()

    @on(Button.Pressed, "#cancel-btn")
    def on_cancel(self) -> None: self.action_go_back()

    @on(Button.Pressed, "#create-btn")
    def on_create(self) -> None:
        """Validate that at least one entry exists, then kick off provisioning."""
        if not self._entries:
            self.app.push_screen(MessageModal(
                "Error", "Add at least one domain entry.", is_error=True))
            return
        # Use "multi-cert" as fallback if the user didn't enter a name
        name = self.query_one("#certmulti-name", Input).value.strip() or "multi-cert"
        self._do_provision(name, list(self._entries))

    @work(exclusive=True)
    async def _do_provision(self, name: str, entries: list[tuple[str, str]]) -> None:
        """
        Call provision_cert_multi_token() and show the secret modal on success.

        Parameters:
          name    — desired token name
          entries — list of (domain, subname) tuples
        """
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: provision_cert_multi_token(self.app.master_token, name, entries),
            )
            secret = result.get("token", "[not returned]")
            # Show the one-time secret in a modal, then go back to TokenListScreen
            self.app.push_screen(NewSecretModal(name, secret))
            self.app.pop_screen()   # close this wizard screen
        except httpx.HTTPStatusError as e:
            self.app.push_screen(MessageModal(
                "API Error",
                f"{e.response.status_code}: {e.response.text}",
                is_error=True,
            ))
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))
