# tui/screens/domains.py
#
# Domain management screens for the desec TUI.
#
# SCREENS IN THIS FILE:
#   CreateDomainModal  — modal form for registering a new domain
#   DomainListScreen   — full-screen view listing all domains
#
# WHAT DOMAINS ARE IN deSEC:
#   deSEC hosts DNS for your domains.  You can register subdomains of dedyn.io
#   for free (e.g. myhost.dedyn.io) or bring your own domain.  Once registered,
#   deSEC becomes the authoritative DNS for that domain.
#
# KEYBOARD SHORTCUTS (DomainListScreen):
#   Esc/b — Back    c — Create Domain    d — Delete Domain
#   m — Manage Records    r — Refresh

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label
from textual import on, work

from core.api import create_domain, delete_domain, list_domains
from tui.widgets import ConfirmModal, MessageModal

if TYPE_CHECKING:
    from tui.app import DeSECApp


# ──────────────────────────────────────────────────────────────────────────────
# CreateDomainModal
# ──────────────────────────────────────────────────────────────────────────────

class CreateDomainModal(ModalScreen[str | None]):
    """
    A simple form modal for registering a new domain.

    Returns the domain name string if the user confirms, or None if cancelled.

    Example domain names:
      myhost.dedyn.io     — a free deSEC-hosted subdomain
      example.com         — a custom domain you own
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        """Build the registration form."""
        with Container(id="modal-box"):
            yield Label("[bold cyan]Register Domain[/]", id="modal-title")
            yield Label("Domain name  [dim](e.g. example.dedyn.io)[/]")
            yield Input(placeholder="example.dedyn.io", id="domain-input")
            with Horizontal(id="modal-buttons"):
                yield Button("Register", id="register-btn", variant="success")
                yield Button("Cancel", id="cancel-btn")

    def on_mount(self) -> None:
        """Focus the domain input for immediate typing."""
        self.query_one("#domain-input", Input).focus()

    @on(Button.Pressed, "#register-btn")
    @on(Input.Submitted, "#domain-input")  # also triggered on Enter
    def do_register(self) -> None:
        """Validate and dismiss with the entered domain name."""
        name = self.query_one("#domain-input", Input).value.strip()
        if not name:
            self.app.push_screen(MessageModal("Error", "Domain name is required.", is_error=True))
            return
        self.dismiss(name)

    @on(Button.Pressed, "#cancel-btn")
    def action_cancel(self) -> None:
        """User cancelled — dismiss with None."""
        self.dismiss(None)


# ──────────────────────────────────────────────────────────────────────────────
# DomainListScreen
# ──────────────────────────────────────────────────────────────────────────────

class DomainListScreen(Screen):
    """
    List all domains registered under the account, with create/delete/records actions.

    Opened from TokenListScreen when the user presses 'm' or the Domains button.
    From here the user can navigate to RRSetScreen to manage DNS records for a domain.
    """

    if TYPE_CHECKING:
        @property
        def app(self) -> DeSECApp: ...   # type: ignore[override]

    BINDINGS = [
        Binding("escape,b", "go_back",       "Back"),
        Binding("c",        "create_domain", "Create"),
        Binding("d",        "delete_domain", "Delete"),
        Binding("m",        "manage_records","Manage Records"),
        Binding("r",        "refresh",       "Refresh"),
    ]

    def __init__(self):
        super().__init__()
        self._domains: list[dict] = []   # current domain list from API

    def compose(self) -> ComposeResult:
        """Build the domain list screen layout."""
        yield Header()
        with Vertical(id="domain-screen"):
            yield Label("[bold cyan]deSEC Domains[/]", id="domain-title")
            yield DataTable(id="domain-table", cursor_type="row")
            with Horizontal(id="domain-actions"):
                yield Button("Create (c)",  id="create-btn",  variant="success")
                yield Button("Records (m)", id="records-btn", variant="primary")
                yield Button("Delete (d)",  id="del-btn",     variant="error")
                yield Button("Refresh (r)", id="ref-btn")
                yield Button("Back (Esc)",  id="back-btn")
        yield Footer()

    def on_mount(self) -> None:
        """Set up table columns and load domain data."""
        table = self.query_one("#domain-table", DataTable)
        table.add_columns("Name", "Created", "Min TTL", "Published")
        self.load_data()

    @work(exclusive=True)
    async def load_data(self) -> None:
        """Fetch the domain list from the API and populate the table."""
        try:
            self._domains = await asyncio.get_event_loop().run_in_executor(
                None, list_domains, self.app.master_token
            )
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))
            return

        table = self.query_one("#domain-table", DataTable)
        table.clear()
        for d in self._domains:
            # [:10] slices ISO 8601 datetime to just the date part: "2024-01-15"
            created = (d.get("created") or "")[:10]
            table.add_row(
                d.get("name", ""),
                created,
                str(d.get("minimum_ttl", "")),
                "[green]Yes[/]" if d.get("published") else "[dim]–[/]",
                key=d.get("name"),  # unique key for row lookup
            )

    def _selected_domain(self) -> dict | None:
        """Return the domain dict for the currently highlighted row, or None."""
        table = self.query_one("#domain-table", DataTable)
        if not self._domains or table.cursor_row < 0 or table.cursor_row >= len(self._domains):
            return None
        return self._domains[table.cursor_row]

    def action_go_back(self) -> None:
        """Return to the previous screen (TokenListScreen)."""
        self.app.pop_screen()

    def action_refresh(self) -> None:
        """Reload domain list from the API."""
        self.load_data()

    def action_create_domain(self) -> None:
        """Open the Create Domain modal."""
        self.app.push_screen(CreateDomainModal(), self._handle_create)

    def _handle_create(self, name: str | None) -> None:
        """Called when CreateDomainModal closes.  `name` is None if user cancelled."""
        if name is None:
            return
        self._do_create_domain(name)

    @work(exclusive=True)
    async def _do_create_domain(self, name: str) -> None:
        """Submit the create-domain API call."""
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: create_domain(self.app.master_token, name)
            )
            self.load_data()  # refresh to show the new domain
        except httpx.HTTPStatusError as e:
            self.app.push_screen(MessageModal(
                "API Error", f"{e.response.status_code}: {e.response.text}", is_error=True))
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))

    def action_delete_domain(self) -> None:
        """Show a destructive-action confirmation before deleting a domain."""
        d = self._selected_domain()
        if not d:
            return
        name = d.get("name", "")

        def _cb_del_dom(ok: bool | None) -> None:
            if ok:
                self._do_delete_domain(name)

        self.app.push_screen(
            ConfirmModal(
                f"Delete domain [bold]{name}[/]?\n"
                f"[red]All DNS records will be permanently removed![/]"
            ),
            _cb_del_dom,
        )

    @work(exclusive=True)
    async def _do_delete_domain(self, name: str) -> None:
        """Submit the delete-domain API call."""
        try:
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: delete_domain(self.app.master_token, name)
            )
            self.load_data()
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))

    def action_manage_records(self) -> None:
        """Navigate to RRSetScreen for the selected domain."""
        d = self._selected_domain()
        if d:
            from tui.screens.rrsets import RRSetScreen
            self.app.push_screen(RRSetScreen(d["name"]))

    # ── Button → action wiring ─────────────────────────────────────────────────

    @on(Button.Pressed, "#create-btn")
    def on_create(self) -> None: self.action_create_domain()
    @on(Button.Pressed, "#records-btn")
    def on_records(self) -> None: self.action_manage_records()
    @on(Button.Pressed, "#del-btn")
    def on_del(self) -> None: self.action_delete_domain()
    @on(Button.Pressed, "#ref-btn")
    def on_ref(self) -> None: self.action_refresh()
    @on(Button.Pressed, "#back-btn")
    def on_back(self) -> None: self.action_go_back()
