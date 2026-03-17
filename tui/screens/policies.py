# tui/screens/policies.py
#
# Policy management screens for the desec TUI.
#
# SCREENS IN THIS FILE:
#   AddPolicyModal  — modal form for adding a new access policy to a token
#   PolicyScreen    — full-screen view listing all policies for one token
#
# WHAT ARE POLICIES?
#   A deSEC "RRset policy" controls which DNS records a token is allowed to
#   read or write.  Each policy entry specifies:
#     domain  — which domain (or any)
#     subname — which subdomain label (or any)
#     type    — which DNS record type (or any)
#     perm_write — True = writes allowed; False = read-only/deny
#
# IMPORTANT CONSTRAINT:
#   deSEC requires that a "default" catch-all policy (all fields = any,
#   perm_write=False) exists BEFORE any scoped policies can be added.
#   The API returns 400 "Policy precedence" if you violate this.
#   PolicyScreen._do_add_policy handles this automatically by detecting the
#   error and offering to auto-create the default policy first.
#
# KEYBOARD SHORTCUTS (PolicyScreen):
#   Esc/b — Back    a — Add Policy    d — Delete Selected    r — Refresh

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Checkbox, DataTable, Footer, Header, Input, Label, Select, Static
from textual import on, work

from core.api import create_policy, delete_policy, list_policies, list_domains
from tui.widgets import ConfirmModal, MessageModal

if TYPE_CHECKING:
    from tui.app import DeSECApp


# ──────────────────────────────────────────────────────────────────────────────
# AddPolicyModal
# ──────────────────────────────────────────────────────────────────────────────

class AddPolicyModal(ModalScreen[dict | None]):
    """
    A form modal for adding a new RRset policy to a token.

    Returns a dict with the policy fields if the user clicks Add Policy,
    or None if they cancel.

    The returned dict contains:
      domain    — str or None (None = any domain)
      subname   — str or None (None = any subname)
      type      — str or None (None = any record type)
      perm_write — bool

    Parameters passed to __init__:
      domains — list of domain names to show in the dropdown
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    # Supported DNS record types shown in the type dropdown.
    # "(any)" means the policy applies regardless of record type.
    RECORD_TYPES = ["(any)", "A", "AAAA", "CNAME", "MX", "NS", "SOA", "TXT", "SRV", "CAA"]

    def __init__(self, domains: list[str]):
        super().__init__()
        self._domains = domains

    def compose(self) -> ComposeResult:
        """Build the add-policy form layout."""
        # Domain dropdown: "(any domain)" option first, then the user's actual domains
        domain_opts = [("(any domain — default policy)", "")] + [(d, d) for d in self._domains]
        # Type dropdown: map "(any)" to empty string value for easy None conversion
        type_opts = [(t, "" if t == "(any)" else t) for t in self.RECORD_TYPES]

        with Container(id="modal-box", classes="wide"):
            yield Label("[bold cyan]Add Policy[/]", id="modal-title")
            with ScrollableContainer():
                yield Label("Domain  [dim](leave as 'any' for the required default policy)[/]")
                # Default to "any domain" — prompts user to create the required default first
                yield Select(options=domain_opts, id="domain-select", value="")

                yield Label("Subname  [dim](leave blank = all subnames)[/]")
                yield Input(placeholder="e.g. _acme-challenge", id="subname-input")

                yield Label("Record type")
                yield Select(options=type_opts, id="type-select", value="")

                yield Checkbox("Allow writes (unchecked = read-only / deny)", id="perm-write")

            with Horizontal(id="modal-buttons"):
                yield Button("Add Policy", id="add-btn", variant="success")
                yield Button("Cancel", id="cancel-btn")

    def on_mount(self) -> None:
        """Focus subname input for quick typing."""
        self.query_one("#subname-input", Input).focus()

    @on(Button.Pressed, "#add-btn")
    def do_add(self) -> None:
        """Collect form values and dismiss with the policy data dict."""
        # Empty domain string → None (means "any domain" = default policy)
        domain = self.query_one("#domain-select", Select).value
        subname = self.query_one("#subname-input", Input).value.strip() or None
        rtype = self.query_one("#type-select", Select).value or None
        perm_write = self.query_one("#perm-write", Checkbox).value

        self.dismiss({
            "domain": domain or None,
            "subname": subname,
            "type": rtype,
            "perm_write": perm_write,
        })

    @on(Button.Pressed, "#cancel-btn")
    def action_cancel(self) -> None:
        """User cancelled — dismiss with None."""
        self.dismiss(None)


# ──────────────────────────────────────────────────────────────────────────────
# PolicyScreen
# ──────────────────────────────────────────────────────────────────────────────

class PolicyScreen(Screen):
    """
    View and manage RRset policies for a single token.

    Opened by TokenListScreen when the user presses 'p' or the Policies button.

    Parameters:
      token_id   — UUID of the token whose policies to display
      token_name — display name for the screen heading
    """

    if TYPE_CHECKING:
        @property
        def app(self) -> DeSECApp: ...   # type: ignore[override]

    BINDINGS = [
        Binding("escape,b", "go_back",      "Back"),
        Binding("a",        "add_policy",   "Add Policy"),
        Binding("d",        "delete_policy","Delete Selected"),
        Binding("r",        "refresh",      "Refresh"),
    ]

    def __init__(self, token_id: str, token_name: str):
        super().__init__()
        self._token_id = token_id
        self._token_name = token_name
        self._policies: list[dict] = []   # current list from API (used for row indexing)
        self._domains: list[str] = []     # available domain names (for AddPolicyModal dropdown)

    def compose(self) -> ComposeResult:
        """Build the policy list screen."""
        yield Header()
        with Vertical(id="policy-screen"):
            yield Label(f"[bold cyan]Policies — {self._token_name}[/]", id="policy-title")
            # Reminder about the deSEC requirement for a default policy
            yield Static(
                "[dim]A deny-all default policy (domain=any, subname=any, type=any, write=No) "
                "must exist before specific policies can be added.[/]",
                id="policy-hint",
            )
            yield DataTable(id="policy-table", cursor_type="row")
            with Horizontal(id="policy-actions"):
                yield Button("Add Policy (a)", id="add-btn", variant="success")
                yield Button("Delete Selected (d)", id="del-btn", variant="error")
                yield Button("Back (Esc)", id="back-btn")
        yield Footer()

    def on_mount(self) -> None:
        """Set up table columns and load data."""
        table = self.query_one("#policy-table", DataTable)
        table.add_columns("ID", "Domain", "Subname", "Type", "Write?")
        self.load_data()

    @work(exclusive=True)
    async def load_data(self) -> None:
        """
        Fetch policies for this token and all domains (for the Add modal dropdown).
        Updates the table after loading.
        """
        token = self.app.master_token
        try:
            # Load both in parallel using gather would be ideal, but run_in_executor
            # with sequential awaits is simpler and fast enough for this use case
            self._policies = await asyncio.get_event_loop().run_in_executor(
                None, list_policies, token, self._token_id
            )
            domains_raw = await asyncio.get_event_loop().run_in_executor(
                None, list_domains, token
            )
            self._domains = [d["name"] for d in domains_raw]
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))
            return

        table = self.query_one("#policy-table", DataTable)
        table.clear()
        for p in self._policies:
            table.add_row(
                p.get("id", ""),
                p.get("domain") or "[dim](any)[/]",
                p.get("subname") or "[dim](any)[/]",
                p.get("type") or "[dim](any)[/]",
                "[green]Yes[/]" if p.get("perm_write") else "[red]No[/]",
                key=p.get("id"),
            )

    def action_go_back(self) -> None:
        """Pop this screen and return to TokenListScreen."""
        self.app.pop_screen()

    def action_refresh(self) -> None:
        """Reload policy data from the API."""
        self.load_data()

    def action_add_policy(self) -> None:
        """Open the Add Policy form."""
        self.app.push_screen(AddPolicyModal(self._domains), self._handle_add_policy)

    def _handle_add_policy(self, result: dict | None) -> None:
        """Called when AddPolicyModal closes."""
        if result is None:
            return
        self._do_add_policy(result)

    @work(exclusive=True)
    async def _do_add_policy(self, data: dict) -> None:
        """
        Submit the create_policy API call.

        Special handling: if deSEC returns 400 with "Policy precedence" in the body,
        it means a default catch-all policy is required first.  We detect this and
        offer to auto-create it, then retry the original policy.
        """
        token = self.app.master_token
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: create_policy(
                    token, self._token_id,
                    data["domain"], data["subname"], data["type"], data["perm_write"],
                ),
            )
            self.load_data()  # refresh to show the new policy
        except httpx.HTTPStatusError as e:
            body = e.response.text
            # Check for the specific deSEC "Policy precedence" constraint error
            if e.response.status_code == 400 and "Policy precedence" in body:
                # Offer to auto-create the default policy and retry
                def _cb_fix(ok: bool | None) -> None:
                    if ok:
                        self._auto_create_default_then_retry(data)

                self.app.push_screen(
                    ConfirmModal(
                        "deSEC requires a [bold]default policy[/] (all fields = any) "
                        "before scoped policies can be added.\n\n"
                        "Auto-create a read-only default policy first, then add yours?"
                    ),
                    _cb_fix,
                )
            else:
                # Any other API error — show the status and body
                self.app.push_screen(MessageModal(
                    "API Error", f"{e.response.status_code}: {body}", is_error=True))
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))

    @work(exclusive=True)
    async def _auto_create_default_then_retry(self, data: dict) -> None:
        """
        Create the required catch-all deny policy, then retry adding the intended policy.

        This is the auto-fix flow triggered by the "Policy precedence" error.
        Step 1: create policy(domain=None, subname=None, type=None, perm_write=False)
        Step 2: create the policy the user actually wanted
        """
        token = self.app.master_token
        try:
            # Step 1: create the required default deny-all policy
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: create_policy(token, self._token_id, None, None, None, False),
            )
            # Step 2: create the user's intended policy
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: create_policy(
                    token, self._token_id,
                    data["domain"], data["subname"], data["type"], data["perm_write"],
                ),
            )
            self.load_data()
        except httpx.HTTPStatusError as e:
            body = e.response.text
            self.app.push_screen(MessageModal(
                "API Error", f"{e.response.status_code}: {body}", is_error=True))
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))

    def action_delete_policy(self) -> None:
        """Show a confirmation dialog before deleting the selected policy."""
        table = self.query_one("#policy-table", DataTable)
        # Guard against empty list or invalid cursor position
        if table.cursor_row < 0 or not self._policies:
            return
        if table.cursor_row >= len(self._policies):
            return
        policy = self._policies[table.cursor_row]
        _pid = policy["id"]

        def _cb_del_pol(ok: bool | None) -> None:
            if ok:
                self._do_delete_policy(_pid)

        self.app.push_screen(ConfirmModal(f"Delete policy [bold]{_pid}[/]?"), _cb_del_pol)

    @work(exclusive=True)
    async def _do_delete_policy(self, policy_id: str) -> None:
        """Submit the delete-policy API call."""
        token = self.app.master_token
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: delete_policy(token, self._token_id, policy_id),
            )
            self.load_data()
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))

    # ── Button → action wiring ─────────────────────────────────────────────────

    @on(Button.Pressed, "#add-btn")
    def on_add(self) -> None: self.action_add_policy()

    @on(Button.Pressed, "#del-btn")
    def on_del(self) -> None: self.action_delete_policy()

    @on(Button.Pressed, "#back-btn")
    def on_back(self) -> None: self.action_go_back()
