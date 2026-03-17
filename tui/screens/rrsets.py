# tui/screens/rrsets.py
#
# DNS record (RRset) management screens for the desec TUI.
#
# SCREENS IN THIS FILE:
#   AddEditRRSetModal  — modal form for adding or editing an RRset
#   RRSetScreen        — full-screen view listing all DNS records for a domain
#
# WHAT IS AN RRSET?
#   An RRset (Resource Record Set) is all DNS records of the same type at
#   the same name.  Example: all A records for "www.example.com" = one RRset.
#   deSEC manages records at the RRset level (not individual records).
#
# RECORD RDATA FORMAT:
#   Each record value ("rdata") must be in the standard DNS RDATA format:
#     A:     "1.2.3.4"
#     AAAA:  "2001:db8::1"
#     CNAME: "target.example.com."   ← note the trailing dot
#     MX:    "10 mail.example.com."  ← priority + space + target
#     TXT:   "\"v=spf1 include:example.com ~all\""  ← quoted string
#
# KEYBOARD SHORTCUTS (RRSetScreen):
#   Esc/b — Back    a — Add record    e — Edit record    d — Delete    r — Refresh

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, ScrollableContainer, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Select, TextArea
from textual import on, work

from core.api import create_rrset, delete_rrset, list_rrsets, update_rrset
from tui.widgets import ConfirmModal, MessageModal

if TYPE_CHECKING:
    from tui.app import DeSECApp


# ──────────────────────────────────────────────────────────────────────────────
# AddEditRRSetModal
# ──────────────────────────────────────────────────────────────────────────────

class AddEditRRSetModal(ModalScreen[dict | None]):
    """
    A form modal for adding a new RRset or editing an existing one.

    Used for both Add and Edit operations.  In Edit mode:
      - The subname and type fields are disabled (you can't change the identity
        of an existing RRset; you'd need to delete and recreate).
      - The TTL and records fields are editable.

    Returns a dict with the RRset fields if the user saves, or None if cancelled:
      {"subname": str, "type": str, "ttl": int, "records": list[str]}

    Constructor parameters:
      subname        — pre-fill subname (blank = apex)
      rtype          — pre-select record type
      ttl            — pre-fill TTL (seconds)
      records        — pre-fill record values (one per line in TextArea)
      editing        — True = editing an existing RRset (disables subname/type)
      existing_names — list of FQDNs already in this domain (for CNAME quick-pick)
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    # Complete list of supported DNS record types
    RECORD_TYPES = [
        "A", "AAAA", "ALIAS", "CAA", "CNAME", "DS", "MX", "NS",
        "PTR", "SOA", "SRV", "SSHFP", "TLSA", "TXT",
    ]

    def __init__(
        self,
        subname: str = "",
        rtype: str = "A",
        ttl: int = 3600,
        records: list[str] | None = None,
        editing: bool = False,
        existing_names: list[str] | None = None,
    ):
        super().__init__()
        self._subname = subname
        self._rtype = rtype
        self._ttl = str(ttl)
        # Join records into multi-line text for the TextArea (one record per line)
        self._records_text = "\n".join(records or [])
        self._editing = editing
        # Sort and deduplicate existing names for the CNAME quick-pick dropdown
        self._existing_names = sorted(set(existing_names or []))

    def compose(self) -> ComposeResult:
        """Build the RRset add/edit form."""
        type_opts = [(t, t) for t in self.RECORD_TYPES]
        title = "[bold cyan]Edit RRset[/]" if self._editing else "[bold cyan]Add RRset[/]"

        # Show the CNAME quick-pick only when CNAME is selected
        cname_hidden = "" if self._rtype == "CNAME" else "hidden"
        pick_opts = [("— type destination manually —", "")] + [
            (n, n) for n in self._existing_names
        ]

        with Container(id="modal-box", classes="wide"):
            yield Label(title, id="modal-title")
            with ScrollableContainer():
                yield Label("Subname  [dim](blank or @ = apex)[/]")
                yield Input(
                    # Show the subname if editing, otherwise start blank
                    value="" if not self._subname else self._subname,
                    placeholder="e.g. www, mail, _acme-challenge  (blank = apex)",
                    id="subname-input",
                    # Disable subname editing when modifying an existing RRset
                    disabled=self._editing,
                )

                yield Label("Record type")
                yield Select(
                    options=type_opts,
                    id="type-select",
                    value=self._rtype,
                    disabled=self._editing,  # can't change type of existing RRset
                )

                yield Label("TTL  [dim](seconds)[/]")
                yield Input(value=self._ttl, placeholder="3600", id="ttl-input")

                yield Label("Records  [dim](one per line, RDATA format)[/]")
                # TextArea is a multi-line editor — each line is one record value
                yield TextArea(self._records_text, id="records-area")

                # CNAME quick-pick: shown only when CNAME type is selected.
                # Lets user pick an existing FQDN instead of typing it manually.
                yield Label(
                    "Quick-pick CNAME target  [dim](auto-fills above)[/]",
                    id="cname-pick-lbl",
                    classes=cname_hidden,
                )
                yield Select(
                    options=pick_opts,
                    id="cname-pick",
                    value="",
                    classes=cname_hidden,
                )

            with Horizontal(id="modal-buttons"):
                yield Button("Save", id="save-btn", variant="success")
                yield Button("Cancel", id="cancel-btn")

    @on(Select.Changed, "#type-select")
    def on_type_changed(self, event: Select.Changed) -> None:
        """
        Show or hide the CNAME quick-pick when the record type changes.

        The "hidden" CSS class (defined in the App CSS) sets display: none.
        We toggle it based on whether CNAME is the selected type.
        """
        is_cname = str(event.value) == "CNAME"
        for wid in ("#cname-pick-lbl", "#cname-pick"):
            # set_class(True, "hidden") adds the class; set_class(False, "hidden") removes it
            self.query_one(wid).set_class(not is_cname, "hidden")

    @on(Select.Changed, "#cname-pick")
    def on_cname_pick(self, event: Select.Changed) -> None:
        """
        When user picks a CNAME target from the dropdown, auto-fill the records TextArea.
        Empty selection (the "type manually" option) is ignored.
        """
        val = str(event.value) if event.value else ""
        if val:
            self.query_one("#records-area", TextArea).load_text(val)

    @on(Button.Pressed, "#save-btn")
    def do_save(self) -> None:
        """
        Validate the form and dismiss with the RRset data.
        Shows error modals for invalid TTL, empty records, or no type selected.
        """
        subname = self.query_one("#subname-input", Input).value.strip()
        # "@" is a conventional alias for the apex — normalize it to empty string
        if subname == "@":
            subname = ""

        # Validate TTL is a valid integer
        try:
            ttl = int(self.query_one("#ttl-input", Input).value.strip())
        except ValueError:
            self.app.push_screen(MessageModal("Error", "TTL must be an integer.", is_error=True))
            return

        # Parse records: split TextArea content on newlines, strip each line, drop empties
        records_text = self.query_one("#records-area", TextArea).text.strip()
        records = [r.strip() for r in records_text.splitlines() if r.strip()]
        if not records:
            self.app.push_screen(MessageModal(
                "Error", "At least one record value is required.", is_error=True))
            return

        rtype = self.query_one("#type-select", Select).value
        if not rtype:
            self.app.push_screen(MessageModal("Error", "Record type is required.", is_error=True))
            return

        self.dismiss({"subname": subname, "type": rtype, "ttl": ttl, "records": records})

    @on(Button.Pressed, "#cancel-btn")
    def action_cancel(self) -> None:
        """User cancelled — dismiss with None."""
        self.dismiss(None)


# ──────────────────────────────────────────────────────────────────────────────
# RRSetScreen
# ──────────────────────────────────────────────────────────────────────────────

class RRSetScreen(Screen):
    """
    View and manage DNS records (RRsets) for a single domain.

    Opened from DomainListScreen when the user selects a domain and presses 'm'.

    Parameters:
      domain — the domain name whose DNS records to display and manage
    """

    if TYPE_CHECKING:
        @property
        def app(self) -> DeSECApp: ...   # type: ignore[override]

    BINDINGS = [
        Binding("escape,b", "go_back",      "Back"),
        Binding("a",        "add_rrset",    "Add"),
        Binding("e",        "edit_rrset",   "Edit"),
        Binding("d",        "delete_rrset", "Delete"),
        Binding("r",        "refresh",      "Refresh"),
    ]

    def __init__(self, domain: str):
        super().__init__()
        self._domain = domain
        self._rrsets: list[dict] = []   # current RRset list from API

    def compose(self) -> ComposeResult:
        """Build the DNS records screen layout."""
        yield Header()
        with Vertical(id="rrset-screen"):
            yield Label(f"[bold cyan]DNS Records — {self._domain}[/]", id="rrset-title")
            yield DataTable(id="rrset-table", cursor_type="row")
            with Horizontal(id="rrset-actions"):
                yield Button("Add (a)",     id="add-btn",  variant="success")
                yield Button("Edit (e)",    id="edit-btn", variant="primary")
                yield Button("Delete (d)",  id="del-btn",  variant="error")
                yield Button("Refresh (r)", id="ref-btn")
                yield Button("Back (Esc)",  id="back-btn")
        yield Footer()

    def on_mount(self) -> None:
        """Set up table columns and load RRset data."""
        table = self.query_one("#rrset-table", DataTable)
        table.add_columns("Subname", "Type", "TTL", "Records")
        self.load_data()

    @work(exclusive=True)
    async def load_data(self) -> None:
        """Fetch all RRsets for this domain and populate the table."""
        try:
            self._rrsets = await asyncio.get_event_loop().run_in_executor(
                None, list_rrsets, self.app.master_token, self._domain
            )
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))
            return

        table = self.query_one("#rrset-table", DataTable)
        table.clear()
        for rr in self._rrsets:
            # Apex records have empty subname — show "@" as a visual hint
            subname = rr.get("subname") or "[dim]@[/]"
            records = rr.get("records", [])
            # Show first record; if there are more, append "(+N more)" hint
            rec_display = records[0] if records else ""
            if len(records) > 1:
                rec_display += f" [dim](+{len(records) - 1} more)[/]"
            table.add_row(
                subname,
                rr.get("type", ""),
                str(rr.get("ttl", "")),
                rec_display,
                # Composite key: subname|type uniquely identifies an RRset
                key=f"{rr.get('subname', '')}|{rr.get('type', '')}",
            )

    def _selected_rrset(self) -> dict | None:
        """Return the RRset dict for the highlighted row, or None."""
        table = self.query_one("#rrset-table", DataTable)
        if not self._rrsets or table.cursor_row < 0 or table.cursor_row >= len(self._rrsets):
            return None
        return self._rrsets[table.cursor_row]

    def action_go_back(self) -> None:
        """Return to DomainListScreen."""
        self.app.pop_screen()

    def action_refresh(self) -> None:
        """Reload records from the API."""
        self.load_data()

    def _existing_fqdns(self, exclude_rr: dict | None = None) -> list[str]:
        """
        Build a list of all FQDNs in this domain for the CNAME quick-pick dropdown.

        Optionally excludes the RRset being edited (so a CNAME doesn't offer itself
        as its own target).

        Returns list of strings like ["www.example.dedyn.io.", "example.dedyn.io."]
        """
        out = []
        for rr in self._rrsets:
            if rr is exclude_rr:
                continue   # skip the record being edited
            sub = rr.get("subname", "")
            # Build the FQDN: subdomain.domain. or domain. (with trailing dot = FQDN)
            out.append(f"{sub}.{self._domain}." if sub else f"{self._domain}.")
        return sorted(set(out))

    def action_add_rrset(self) -> None:
        """Open AddEditRRSetModal in Add mode."""
        self.app.push_screen(
            AddEditRRSetModal(existing_names=self._existing_fqdns()),
            self._handle_add,
        )

    def _handle_add(self, result: dict | None) -> None:
        """Called when the Add RRset modal closes."""
        if result is None:
            return
        self._do_save_rrset(result, editing=False)

    def action_edit_rrset(self) -> None:
        """Open AddEditRRSetModal in Edit mode, pre-filled with the selected record."""
        rr = self._selected_rrset()
        if not rr:
            return
        self.app.push_screen(
            AddEditRRSetModal(
                subname=rr.get("subname", ""),
                rtype=rr.get("type", "A"),
                ttl=rr.get("ttl", 3600),
                records=rr.get("records", []),
                editing=True,
                existing_names=self._existing_fqdns(exclude_rr=rr),
            ),
            self._handle_edit,
        )

    def _handle_edit(self, result: dict | None) -> None:
        """Called when the Edit RRset modal closes."""
        if result is None:
            return
        self._do_save_rrset(result, editing=True)

    @work(exclusive=True)
    async def _do_save_rrset(self, data: dict, editing: bool) -> None:
        """
        Submit create_rrset() or update_rrset() depending on whether we're editing.

        Parameters:
          data    — dict with subname, type, ttl, records
          editing — True = call update; False = call create
        """
        token = self.app.master_token
        try:
            if editing:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: update_rrset(
                        token, self._domain,
                        data["subname"], data["type"], data["ttl"], data["records"],
                    ),
                )
            else:
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: create_rrset(
                        token, self._domain,
                        data["subname"], data["type"], data["ttl"], data["records"],
                    ),
                )
            self.load_data()   # refresh the table to show the change
        except httpx.HTTPStatusError as e:
            self.app.push_screen(MessageModal(
                "API Error", f"{e.response.status_code}: {e.response.text}", is_error=True))
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))

    def action_delete_rrset(self) -> None:
        """Show a confirmation dialog before deleting the selected RRset."""
        rr = self._selected_rrset()
        if not rr:
            return
        # Use "@" as the display name for apex records
        display_subname = rr.get("subname") or "@"
        rtype = rr.get("type", "")
        _sub = rr.get("subname", "")

        def _cb_del_rr(ok: bool | None) -> None:
            if ok:
                self._do_delete_rrset(_sub, rtype)

        self.app.push_screen(
            ConfirmModal(
                f"Delete [bold]{display_subname} {rtype}[/] from [bold]{self._domain}[/]?"
            ),
            _cb_del_rr,
        )

    @work(exclusive=True)
    async def _do_delete_rrset(self, subname: str, rtype: str) -> None:
        """Submit the delete_rrset API call."""
        try:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: delete_rrset(self.app.master_token, self._domain, subname, rtype),
            )
            self.load_data()
        except Exception as e:
            self.app.push_screen(MessageModal("Error", str(e), is_error=True))

    # ── Button → action wiring ─────────────────────────────────────────────────

    @on(Button.Pressed, "#add-btn")
    def on_add(self) -> None: self.action_add_rrset()
    @on(Button.Pressed, "#edit-btn")
    def on_edit(self) -> None: self.action_edit_rrset()
    @on(Button.Pressed, "#del-btn")
    def on_del(self) -> None: self.action_delete_rrset()
    @on(Button.Pressed, "#ref-btn")
    def on_ref(self) -> None: self.action_refresh()
    @on(Button.Pressed, "#back-btn")
    def on_back(self) -> None: self.action_go_back()
