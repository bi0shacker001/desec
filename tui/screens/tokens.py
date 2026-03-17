# tui/screens/tokens.py
#
# Token management screens for the desec TUI.
#
# SCREENS IN THIS FILE:
#   CreateTokenModal  — modal form for creating a new API token
#   TokenListScreen   — main screen; lists all tokens with action buttons
#
# KEYBOARD SHORTCUTS (TokenListScreen):
#   n — New Token      p — View Policies   m — Manage Domains
#   k — DDNS Key       c — Cert Key        u — Multi-Cert Key
#   d — Delete Token   r — Refresh         q — Quit
#
# NAVIGATION FROM HERE:
#   p → PolicyScreen (policies.py)
#   m → DomainListScreen (domains.py)
#   k → DdnsAddModal (provision.py)
#   c → CertAddModal (provision.py)
#   u → CertMultiScreen (provision.py)

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label
from textual import on, work

from core.api import create_token, delete_token, list_tokens, list_domains
from tui.widgets import ConfirmModal, MessageModal, NewSecretModal

if TYPE_CHECKING:
    from tui.app import DeSECApp


# ──────────────────────────────────────────────────────────────────────────────
# CreateTokenModal
# ──────────────────────────────────────────────────────────────────────────────

class CreateTokenModal(ModalScreen[dict | None]):
    """
    A form modal for creating a new API token.

    Returns a dict of form values to the caller if the user clicks Create,
    or None if they cancel.

    The dict contains:
      name               — token label
      perm_manage_tokens — bool: can this token create/delete other tokens?
      perm_create_domain — bool: can this token register new domains?
      perm_delete_domain — bool: can this token delete domains?
      auto_policy        — bool: auto-create permissive policy on domain creation?
      allowed_subnets    — list of CIDR strings (empty = any IP allowed)
      max_unused_period  — ISO 8601 duration string or None
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        """Build the token creation form."""
        with Container(id="modal-box", classes="wide"):
            yield Label("[bold cyan]Create New Token[/]", id="modal-title")
            # ScrollableContainer allows the form to scroll if the terminal is short.
            # IMPORTANT: Use height: auto (not 1fr) or children collapse in Textual 8.
            with ScrollableContainer():
                yield Label("Token name")
                yield Input(placeholder="e.g. certbot-example.com", id="name-input")

                yield Label("Allowed subnets (comma-separated, leave blank for any)")
                yield Input(
                    placeholder="e.g. 203.0.113.0/24, 2001:db8::/32",
                    id="subnets-input",
                )

                yield Label("Max unused period (ISO 8601 duration, e.g. P90D — blank = none)")
                yield Input(placeholder="P90D", id="unused-input")

                # Permission checkboxes — all default to unchecked (least privilege)
                yield Checkbox("Allow managing tokens", id="perm-tokens")
                yield Checkbox("Allow creating domains", id="perm-create")
                yield Checkbox("Allow deleting domains", id="perm-delete")
                yield Checkbox(
                    "Auto-create permissive policy on domain creation (auto_policy)",
                    id="auto-policy",
                )

            with Horizontal(id="modal-buttons"):
                yield Button("Create", id="create-btn", variant="success")
                yield Button("Cancel", id="cancel-btn")

    def on_mount(self) -> None:
        """Focus the name field so the user can start typing immediately."""
        self.query_one("#name-input", Input).focus()

    @on(Button.Pressed, "#create-btn")
    def do_create(self) -> None:
        """
        Validate the form and dismiss with the collected values.
        If the token name is empty, show an error instead.
        """
        name = self.query_one("#name-input", Input).value.strip()
        if not name:
            # Token name is required — can't create a nameless token
            self.app.push_screen(MessageModal("Error", "Token name is required.", is_error=True))
            return

        # Parse allowed_subnets: split on comma, strip whitespace, ignore empty strings
        subnets_raw = self.query_one("#subnets-input", Input).value.strip()
        subnets = [s.strip() for s in subnets_raw.split(",") if s.strip()] if subnets_raw else []

        # Empty unused period = None (no expiry)
        unused = self.query_one("#unused-input", Input).value.strip() or None

        self.dismiss({
            "name": name,
            "perm_manage_tokens": self.query_one("#perm-tokens", Checkbox).value,
            "perm_create_domain": self.query_one("#perm-create", Checkbox).value,
            "perm_delete_domain": self.query_one("#perm-delete", Checkbox).value,
            "auto_policy": self.query_one("#auto-policy", Checkbox).value,
            "allowed_subnets": subnets,
            "max_unused_period": unused,
        })

    @on(Button.Pressed, "#cancel-btn")
    def action_cancel(self) -> None:
        """User cancelled — dismiss with None so caller knows to do nothing."""
        self.dismiss(None)


# ──────────────────────────────────────────────────────────────────────────────
# TokenListScreen
# ──────────────────────────────────────────────────────────────────────────────

class TokenListScreen(Screen):
    """
    Main screen — displays all API tokens for the account and provides actions.

    This is the "home" screen after login.  From here the user can:
      - Create new tokens
      - View and edit token policies
      - Navigate to domain management
      - Run provisioning wizards (DDNS key, cert key, multi-cert key)
      - Delete tokens
    """

    if TYPE_CHECKING:
        @property
        def app(self) -> DeSECApp: ...   # type: ignore[override]

    BINDINGS = [
        Binding("n", "new_token",      "New Token"),
        Binding("p", "view_policies",  "Policies"),
        Binding("m", "manage_domains", "Domains"),
        Binding("k", "ddns_key",       "DDNS Key"),
        Binding("c", "cert_key",       "Cert Key"),
        Binding("u", "cert_multi",     "Multi-Cert"),
        Binding("d", "delete_token",   "Delete"),
        Binding("r", "refresh",        "Refresh"),
        Binding("q", "quit_app",       "Quit"),
    ]

    def compose(self) -> ComposeResult:
        """Build the token list screen layout."""
        yield Header()
        with Vertical(id="token-screen"):
            yield Label("[bold cyan]deSEC API Tokens[/]", id="token-title")
            # DataTable shows the list of tokens.
            # cursor_type="row" means the whole row is highlighted (not a cell).
            yield DataTable(id="token-table", cursor_type="row")
            with Horizontal(id="token-actions"):
                yield Button("New Token (n)",  id="new-btn",  variant="success")
                yield Button("Policies (p)",   id="pol-btn",  variant="primary")
                yield Button("Domains (m)",    id="dom-btn")
                yield Button("DDNS Key (k)",   id="ddns-btn", variant="warning")
                yield Button("Cert Key (c)",   id="cert-btn", variant="warning")
                yield Button("Multi-Cert (u)", id="mcrt-btn", variant="warning")
                yield Button("Delete (d)",     id="del-btn",  variant="error")
                yield Button("Refresh (r)",    id="ref-btn")
        yield Footer()

    def on_mount(self) -> None:
        """Set up the table columns and load data after the screen appears."""
        table = self.query_one("#token-table", DataTable)
        table.add_columns(
            "Name", "ID", "Manage Tokens?", "Create Domain?", "Delete Domain?",
            "Subnets", "Policies?",
        )
        self.load_tokens()

    @work(exclusive=True)
    async def load_tokens(self) -> None:
        """
        Fetch all tokens from the deSEC API and populate the table.

        Uses run_in_executor to run the blocking HTTP call off the main thread.
        @work(exclusive=True) prevents multiple simultaneous loads if the user
        hammers the refresh button.
        """
        try:
            tokens = await asyncio.get_event_loop().run_in_executor(
                None, list_tokens, self.app.master_token
            )
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))
            return

        # Store on the app so _selected_token() can reference by index
        self.app._tokens = tokens

        # Clear existing rows and re-populate
        table = self.query_one("#token-table", DataTable)
        table.clear()
        for t in tokens:
            subnets = ", ".join(t.get("allowed_subnets") or []) or "[dim]any[/]"
            has_policies = "[green]Yes[/]" if t.get("policies") else "[dim]–[/]"
            table.add_row(
                t.get("name") or "[dim]unnamed[/]",
                t.get("id", ""),
                "[green]Yes[/]" if t.get("perm_manage_tokens") else "[dim]No[/]",
                "[green]Yes[/]" if t.get("perm_create_domain")  else "[dim]No[/]",
                "[green]Yes[/]" if t.get("perm_delete_domain")  else "[dim]No[/]",
                subnets,
                has_policies,
                key=t.get("id"),  # unique key used for row lookup
            )

    def _selected_token(self) -> dict | None:
        """
        Return the token dict for the currently highlighted row, or None.

        DataTable.cursor_row is 0-based and -1 when nothing is selected.
        We guard against out-of-bounds in case the list is empty.
        """
        table = self.query_one("#token-table", DataTable)
        tokens = getattr(self.app, "_tokens", [])
        if not tokens or table.cursor_row < 0 or table.cursor_row >= len(tokens):
            return None
        return tokens[table.cursor_row]

    # ── Action handlers ───────────────────────────────────────────────────────

    def action_new_token(self) -> None:
        """Open the Create Token form."""
        self.app.push_screen(CreateTokenModal(), self._handle_new_token)

    def _handle_new_token(self, result: dict | None) -> None:
        """Called when the Create Token modal closes.  `result` is None if cancelled."""
        if result is None:
            return
        self._do_create_token(result)

    @work(exclusive=True)
    async def _do_create_token(self, data: dict) -> None:
        """Submit the create-token API call and show the secret modal on success."""
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: create_token(
                    self.app.master_token,
                    data["name"],
                    data["perm_manage_tokens"],
                    data["perm_create_domain"],
                    data["perm_delete_domain"],
                    data["allowed_subnets"],
                    data["max_unused_period"],
                    data["auto_policy"],
                ),
            )
            # The secret is only returned at creation time — show it immediately
            secret = result.get("token", "[not returned]")
            self.app.push_screen(NewSecretModal(data["name"], secret))
            self.load_tokens()  # refresh the list to show the new token
        except httpx.HTTPStatusError as e:
            self.app.push_screen(MessageModal(
                "API Error",
                f"{e.response.status_code}: {e.response.text}",
                is_error=True,
            ))
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))

    def action_view_policies(self) -> None:
        """Navigate to the PolicyScreen for the selected token."""
        t = self._selected_token()
        if t:
            from tui.screens.policies import PolicyScreen
            self.app.push_screen(PolicyScreen(t["id"], t.get("name", t["id"])))

    def action_manage_domains(self) -> None:
        """Navigate to the DomainListScreen."""
        from tui.screens.domains import DomainListScreen
        self.app.push_screen(DomainListScreen())

    # ── Provisioning wizard launchers ─────────────────────────────────────────

    def action_ddns_key(self) -> None:
        """Load domains then open the DDNS provisioning wizard."""
        self._load_domains_then_open("ddns")

    def action_cert_key(self) -> None:
        """Load domains then open the single-cert provisioning wizard."""
        self._load_domains_then_open("cert")

    def action_cert_multi(self) -> None:
        """Load domains then open the multi-cert provisioning screen."""
        self._load_domains_then_open("multi")

    @work(exclusive=True)
    async def _load_domains_then_open(self, target: str) -> None:
        """
        Fetch the list of domains, then open the requested provisioning screen.

        We load domains first because the wizards show a dropdown of available
        domains — they need the list before they can render.

        Parameters:
          target — "ddns", "cert", or "multi"
        """
        try:
            domains = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: [d["name"] for d in list_domains(self.app.master_token)],
            )
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))
            return

        if not domains:
            # Can't provision a scoped key for a domain that doesn't exist yet
            self.app.push_screen(MessageModal(
                "No Domains",
                "Register at least one domain before creating scoped keys.",
            ))
            return

        from tui.screens.provision import DdnsAddModal, CertAddModal, CertMultiScreen

        if target == "ddns":
            self.app.push_screen(DdnsAddModal(domains), self._handle_ddns)
        elif target == "cert":
            self.app.push_screen(CertAddModal(domains), self._handle_cert)
        else:
            self.app.push_screen(CertMultiScreen(domains))

    def _handle_ddns(self, result: dict | None) -> None:
        """Called when DdnsAddModal closes.  Kick off provisioning if not cancelled."""
        if result:
            self._do_provision_ddns(result)

    def _handle_cert(self, result: dict | None) -> None:
        """Called when CertAddModal closes.  Kick off provisioning if not cancelled."""
        if result:
            self._do_provision_cert(result)

    @work(exclusive=True)
    async def _do_provision_ddns(self, data: dict) -> None:
        """Run the DDNS provisioning API calls and show the resulting secret."""
        from core.api import provision_ddns_token
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: provision_ddns_token(
                    self.app.master_token, data["name"],
                    data["domain"], data["subname"],
                    data["ipv4"], data["ipv6"],
                ),
            )
            secret = result.get("token", "[not returned]")
            self.app.push_screen(NewSecretModal(data["name"], secret))
            self.load_tokens()
        except httpx.HTTPStatusError as e:
            self.app.push_screen(MessageModal(
                "API Error", f"{e.response.status_code}: {e.response.text}", is_error=True))
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))

    @work(exclusive=True)
    async def _do_provision_cert(self, data: dict) -> None:
        """Run the single-cert provisioning API calls and show the resulting secret."""
        from core.api import provision_cert_token
        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: provision_cert_token(
                    self.app.master_token, data["name"],
                    data["domain"], data["subname"],
                    data["ipv4"], data["ipv6"], data["cname"],
                ),
            )
            secret = result.get("token", "[not returned]")
            self.app.push_screen(NewSecretModal(data["name"], secret))
            self.load_tokens()
        except httpx.HTTPStatusError as e:
            self.app.push_screen(MessageModal(
                "API Error", f"{e.response.status_code}: {e.response.text}", is_error=True))
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))

    # ── Delete token ──────────────────────────────────────────────────────────

    def action_delete_token(self) -> None:
        """Show a confirmation dialog before deleting the selected token."""
        t = self._selected_token()
        if not t:
            return
        _tid = t["id"]

        def _cb_del_tok(ok: bool | None) -> None:
            # ok is True if user clicked Yes, False/None if No or cancelled
            if ok:
                self._do_delete(_tid)

        self.app.push_screen(
            ConfirmModal(f"Delete token [bold]{t.get('name', _tid)}[/]?"),
            _cb_del_tok,
        )

    @work(exclusive=True)
    async def _do_delete(self, token_id: str) -> None:
        """Submit the delete-token API call."""
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: delete_token(self.app.master_token, token_id)
            )
            self.load_tokens()  # refresh to remove the deleted token from the list
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))

    def action_refresh(self) -> None:
        """Reload the token list from the API."""
        self.load_tokens()

    def action_quit_app(self) -> None:
        """Exit the TUI entirely."""
        self.app.exit()

    # ── Button → action wiring ────────────────────────────────────────────────
    # Each on(Button.Pressed) handler maps a button click to the corresponding action.
    # This allows both keyboard shortcuts and mouse clicks to work.

    @on(Button.Pressed, "#new-btn")
    def on_new(self)  -> None: self.action_new_token()
    @on(Button.Pressed, "#pol-btn")
    def on_pol(self)  -> None: self.action_view_policies()
    @on(Button.Pressed, "#dom-btn")
    def on_dom(self)  -> None: self.action_manage_domains()
    @on(Button.Pressed, "#ddns-btn")
    def on_ddns(self) -> None: self.action_ddns_key()
    @on(Button.Pressed, "#cert-btn")
    def on_cert(self) -> None: self.action_cert_key()
    @on(Button.Pressed, "#mcrt-btn")
    def on_mcrt(self) -> None: self.action_cert_multi()
    @on(Button.Pressed, "#del-btn")
    def on_del(self)  -> None: self.action_delete_token()
    @on(Button.Pressed, "#ref-btn")
    def on_ref(self)  -> None: self.action_refresh()
