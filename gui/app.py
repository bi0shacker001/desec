# gui/app.py
#
# Full PySide6 Qt GUI for the deSEC DNS manager.
#
# HOW THIS FILE IS ORGANISED:
#   1. Worker threads  — all API calls run in QThread subclasses so the UI
#                        never freezes while waiting for network responses.
#   2. Dialogs         — modal windows for login, create token, create domain,
#                        add/edit record, add policy, provisioning wizards.
#   3. Page widgets    — TokensPage, DomainsPage, RRSetsPage, PoliciesPage.
#                        Each page owns its own table and toolbar.
#   4. MainWindow      — QMainWindow that holds a QListWidget (left nav) and a
#                        QStackedWidget (right content).  Switches pages on nav
#                        selection.
#   5. main()          — entry point called by desec.py --gui.
#
# DEPENDENCIES:
#   PySide6    pip install PySide6
#   httpx      pip install httpx   (used by core.api)
#
# IF THE GUI FAILS TO START:
#   - ImportError on PySide6  → pip install PySide6
#   - "No module named core"  → run from the programs/desec/ directory, or make
#     sure sys.path includes that directory (desec.py handles this at startup)
#   - API calls failing       → check ~/.config/mech-goodies/desec.env for a
#     valid DESEC_TOKEN value

from __future__ import annotations

import sys
from typing import Any

# ── PySide6 imports ──────────────────────────────────────────────────────────
# All Qt classes come from PySide6 sub-packages.
# Qt<X>Widgets holds the visual widget classes.
# Qt<X>Core holds non-visual Qt infrastructure (threads, signals, timers).
try:
    from PySide6.QtCore import (
        Qt,           # namespace of constants (alignment, window flags, etc.)
        QThread,      # base class for worker threads
        Signal,       # type-safe event/callback system between objects
        QObject,      # base class for all Qt objects (needed for Signal)
    )
    from PySide6.QtWidgets import (
        QApplication,       # root Qt object — every Qt app needs exactly one
        QMainWindow,        # top-level window with menu bar + status bar
        QWidget,            # base class for all visual widgets
        QDialog,            # modal window base class
        QVBoxLayout,        # vertical stacking layout
        QHBoxLayout,        # horizontal stacking layout
        QLabel,             # text / image display widget
        QLineEdit,          # single-line text input
        QPushButton,        # clickable button
        QTableWidget,       # spreadsheet-style table
        QTableWidgetItem,   # a single cell in a QTableWidget
        QListWidget,        # single-column list (used for left nav)
        QListWidgetItem,    # one row in a QListWidget
        QStackedWidget,     # container that shows one child widget at a time
        QSplitter,          # resizable divider between two widgets
        QToolBar,           # row of buttons above a content area
        QComboBox,          # drop-down selector
        QMessageBox,        # standard info / error popup
        QDialogButtonBox,   # standard OK / Cancel button row
        QFormLayout,        # two-column label+field layout for forms
        QInputDialog,       # built-in single-value input dialog
        QHeaderView,        # table header (lets us set column resize modes)
        QSizePolicy,        # controls how a widget grows/shrinks
        QStatusBar,         # status bar at the bottom of the main window
        QFrame,             # plain widget often used as a horizontal rule
    )
    from PySide6.QtGui import (
        QFont,          # font specification
        QColor,         # RGB colour
        QPalette,       # colour scheme
        QAction,        # menu / toolbar action
    )
except ImportError:
    # PySide6 is an optional dependency — tell the user how to install it.
    print(
        "PySide6 is required for the GUI.  Install it with:\n"
        "  pip install PySide6\n\n"
        "Or use the TUI instead (no extra dependencies beyond textual):\n"
        "  python desec.py",
        file=sys.stderr,
    )
    sys.exit(1)

# ── deSEC core imports ────────────────────────────────────────────────────────
# These are the same functions the TUI uses — no logic is duplicated here.
from core.api import (
    list_tokens, create_token, delete_token,
    list_policies, create_policy, delete_policy,
    list_domains, create_domain, delete_domain,
    list_rrsets, create_rrset, update_rrset, delete_rrset,
)
from core.env import load_env, save_env


# ─────────────────────────────────────────────────────────────────────────────
# Section 1 — Worker threads
#
# Every API call runs in a QThread so the GUI stays responsive.
# Pattern: create a worker, connect its signals to slots in the calling widget,
# then call worker.start().  The worker emits result(...) on success or
# error(str) on failure.
# ─────────────────────────────────────────────────────────────────────────────

class _ApiWorker(QThread):
    """
    Base class for all API worker threads.

    Subclasses override run() to do the actual API call and emit result() or
    error() when done.  Do not call API functions on the main thread — Qt
    will freeze the entire UI while waiting for the network.
    """
    # result carries whatever the API returned (list, dict, None, etc.)
    result: Signal = Signal(object)
    # error carries a human-readable error message string
    error:  Signal = Signal(str)

    def _emit_error(self, exc: Exception) -> None:
        """Format an exception and emit error().  Called from run() on failure."""
        msg = str(exc)
        # httpx errors include a lot of noise — pull out just the status/body
        if hasattr(exc, "response") and exc.response is not None:  # type: ignore[union-attr]
            try:
                body = exc.response.text[:300]  # type: ignore[union-attr]
                msg = f"HTTP {exc.response.status_code}: {body}"  # type: ignore[union-attr]
            except Exception:
                pass
        self.error.emit(msg)


class ListTokensWorker(_ApiWorker):
    """Fetch all API tokens for the current master token."""
    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    def run(self) -> None:
        try:
            self.result.emit(list_tokens(self._token))
        except Exception as exc:
            self._emit_error(exc)


class DeleteTokenWorker(_ApiWorker):
    """Delete a single token by ID."""
    def __init__(self, master: str, token_id: str) -> None:
        super().__init__()
        self._master   = master
        self._token_id = token_id

    def run(self) -> None:
        try:
            delete_token(self._master, self._token_id)
            self.result.emit(None)
        except Exception as exc:
            self._emit_error(exc)


class CreateTokenWorker(_ApiWorker):
    """Create a new token with optional name."""
    def __init__(self, master: str, name: str) -> None:
        super().__init__()
        self._master = master
        self._name   = name

    def run(self) -> None:
        try:
            self.result.emit(create_token(self._master, name=self._name or None))
        except Exception as exc:
            self._emit_error(exc)


class ListDomainsWorker(_ApiWorker):
    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token

    def run(self) -> None:
        try:
            self.result.emit(list_domains(self._token))
        except Exception as exc:
            self._emit_error(exc)


class CreateDomainWorker(_ApiWorker):
    def __init__(self, token: str, name: str) -> None:
        super().__init__()
        self._token = token
        self._name  = name

    def run(self) -> None:
        try:
            self.result.emit(create_domain(self._token, self._name))
        except Exception as exc:
            self._emit_error(exc)


class DeleteDomainWorker(_ApiWorker):
    def __init__(self, token: str, domain: str) -> None:
        super().__init__()
        self._token  = token
        self._domain = domain

    def run(self) -> None:
        try:
            delete_domain(self._token, self._domain)
            self.result.emit(None)
        except Exception as exc:
            self._emit_error(exc)


class ListRRSetsWorker(_ApiWorker):
    def __init__(self, token: str, domain: str) -> None:
        super().__init__()
        self._token  = token
        self._domain = domain

    def run(self) -> None:
        try:
            self.result.emit(list_rrsets(self._token, self._domain))
        except Exception as exc:
            self._emit_error(exc)


class DeleteRRSetWorker(_ApiWorker):
    def __init__(self, token: str, domain: str, subname: str, rtype: str) -> None:
        super().__init__()
        self._token   = token
        self._domain  = domain
        self._subname = subname
        self._rtype   = rtype

    def run(self) -> None:
        try:
            delete_rrset(self._token, self._domain, self._subname, self._rtype)
            self.result.emit(None)
        except Exception as exc:
            self._emit_error(exc)


class UpsertRRSetWorker(_ApiWorker):
    """Create or replace a DNS record set."""
    def __init__(self, token: str, domain: str, subname: str, rtype: str,
                 ttl: int, records: list[str]) -> None:
        super().__init__()
        self._token   = token
        self._domain  = domain
        self._subname = subname
        self._rtype   = rtype
        self._ttl     = ttl
        self._records = records

    def run(self) -> None:
        try:
            # Try update first; fall back to create if the record doesn't exist.
            # update_rrset raises HTTPStatusError 404 when missing.
            try:
                r = update_rrset(self._token, self._domain, self._subname,
                                 self._rtype, self._ttl, self._records)
            except Exception:
                r = create_rrset(self._token, self._domain, self._subname,
                                 self._rtype, self._ttl, self._records)
            self.result.emit(r)
        except Exception as exc:
            self._emit_error(exc)


class ListPoliciesWorker(_ApiWorker):
    def __init__(self, master: str, token_id: str) -> None:
        super().__init__()
        self._master   = master
        self._token_id = token_id

    def run(self) -> None:
        try:
            self.result.emit(list_policies(self._master, self._token_id))
        except Exception as exc:
            self._emit_error(exc)


class DeletePolicyWorker(_ApiWorker):
    def __init__(self, master: str, token_id: str, policy_id: str) -> None:
        super().__init__()
        self._master    = master
        self._token_id  = token_id
        self._policy_id = policy_id

    def run(self) -> None:
        try:
            delete_policy(self._master, self._token_id, self._policy_id)
            self.result.emit(None)
        except Exception as exc:
            self._emit_error(exc)


class CreatePolicyWorker(_ApiWorker):
    def __init__(self, master: str, token_id: str, domain: str,
                 subname: str, rtype: str, perm_write: bool) -> None:
        super().__init__()
        self._master     = master
        self._token_id   = token_id
        self._domain     = domain
        self._subname    = subname
        self._rtype      = rtype
        self._perm_write = perm_write

    def run(self) -> None:
        try:
            self.result.emit(
                create_policy(self._master, self._token_id,
                              domain=self._domain or None,
                              subname=self._subname or None,
                              rtype=self._rtype or None,
                              perm_write=self._perm_write)
            )
        except Exception as exc:
            self._emit_error(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Section 2 — Dialogs
# ─────────────────────────────────────────────────────────────────────────────

def _err(parent: QWidget, msg: str) -> None:
    """Show a modal error box.  Helper to avoid repeating QMessageBox boilerplate."""
    QMessageBox.critical(parent, "Error", msg)


def _info(parent: QWidget, title: str, msg: str) -> None:
    QMessageBox.information(parent, title, msg)


class LoginDialog(QDialog):
    """
    Modal dialog shown at startup if no token is saved.

    The user types their deSEC API token.  On Accept, the token is returned
    via .token.  On Reject (Cancel), the app exits.

    The token is verified by calling list_tokens() synchronously — this is
    the one place where a blocking call on the main thread is acceptable
    because the app cannot function without a valid token, and no UI is up yet.
    """

    def __init__(self, existing_token: str = "") -> None:
        super().__init__()
        self.token: str = ""          # set on successful login
        self.setWindowTitle("deSEC — Connect")
        self.resize(420, 160)

        layout = QVBoxLayout(self)

        # ── Header label ─────────────────────────────────────────────────────
        header = QLabel("<b>Enter your deSEC API token</b>")
        header.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(header)

        hint = QLabel(
            "Generate a token at <a href='https://desec.io/tokens'>desec.io/tokens</a>.<br>"
            "The token is saved to ~/.config/mech-goodies/desec.env."
        )
        hint.setOpenExternalLinks(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)

        # ── Token input ───────────────────────────────────────────────────────
        self._input = QLineEdit()
        self._input.setPlaceholderText("Token …")
        self._input.setEchoMode(QLineEdit.EchoMode.Password)  # hide the token while typing
        if existing_token:
            self._input.setText(existing_token)
        layout.addWidget(self._input)

        # ── Buttons ───────────────────────────────────────────────────────────
        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._try_login)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

        # Press Enter in the token field to attempt login
        self._input.returnPressed.connect(self._try_login)

    def _try_login(self) -> None:
        """
        Called when the user clicks OK or presses Enter.

        Validates the token against the live deSEC API.  On success, saves it
        to desec.env and sets self.token.  On failure, shows an error and lets
        the user try again.
        """
        tok = self._input.text().strip()
        if not tok:
            _err(self, "Please enter a token.")
            return

        # Verify the token by trying to list tokens (a read-only call)
        try:
            list_tokens(tok)
        except Exception as exc:
            _err(self, f"Token verification failed:\n{exc}")
            return

        # Save for next launch
        env = load_env()
        env["DESEC_TOKEN"] = tok
        save_env(env)

        self.token = tok
        self.accept()


class CreateTokenDialog(QDialog):
    """Form to create a new API token.  Returns the new token dict on accept."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Token")
        self.resize(340, 120)

        form = QFormLayout(self)

        self._name = QLineEdit()
        self._name.setPlaceholderText("e.g. my-script (optional)")
        form.addRow("Token name:", self._name)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    @property
    def name(self) -> str:
        return self._name.text().strip()


class AddRRSetDialog(QDialog):
    """
    Form for adding or editing a DNS record set (RRset).

    An RRset is a group of DNS records with the same name, type, and TTL.
    For example: @ IN A 300 [1.2.3.4, 5.6.7.8]

    Fields:
      subname — the hostname part (@, www, mail, _dmarc, etc.)
      type    — record type (A, AAAA, CNAME, MX, TXT, NS, CAA, SRV, TLSA, …)
      ttl     — time-to-live in seconds (how long resolvers cache this record)
      records — one record per line (e.g. one IP per line for an A record)
    """

    COMMON_TYPES = ["A", "AAAA", "CNAME", "MX", "NS", "TXT", "CAA", "SRV",
                    "TLSA", "PTR", "SPF", "SSHFP", "DS"]

    def __init__(self, parent: QWidget, domain: str,
                 existing: dict[str, Any] | None = None) -> None:
        super().__init__(parent)
        self._edit_mode = existing is not None
        self.setWindowTitle("Edit Record" if self._edit_mode else "Add Record")
        self.resize(460, 300)

        form = QFormLayout(self)

        # Subname — the left-hand side of the record name
        self._subname = QLineEdit()
        self._subname.setPlaceholderText("@ for the root, or subdomain like www")
        form.addRow("Subname:", self._subname)

        # Record type drop-down + free-entry combo
        self._rtype = QComboBox()
        self._rtype.setEditable(True)
        for t in self.COMMON_TYPES:
            self._rtype.addItem(t)
        form.addRow("Type:", self._rtype)

        # TTL
        self._ttl = QLineEdit()
        self._ttl.setPlaceholderText("3600")
        self._ttl.setText("3600")
        form.addRow("TTL (seconds):", self._ttl)

        # Records — one per line
        from PySide6.QtWidgets import QPlainTextEdit
        self._records = QPlainTextEdit()
        self._records.setPlaceholderText("One record value per line.\n"
                                          "A:     1.2.3.4\n"
                                          "CNAME: target.example.com.\n"
                                          "TXT:   \"v=spf1 …\"")
        self._records.setFixedHeight(120)
        form.addRow("Records:", self._records)

        # Pre-fill when editing
        if existing:
            self._subname.setText(existing.get("subname", ""))
            idx = self._rtype.findText(existing.get("type", "A"))
            if idx >= 0:
                self._rtype.setCurrentIndex(idx)
            else:
                self._rtype.setEditText(existing.get("type", "A"))
            self._ttl.setText(str(existing.get("ttl", 3600)))
            # records is a list of strings in the API response
            self._records.setPlainText("\n".join(existing.get("records", [])))

        # Lock subname + type when editing (they're part of the primary key)
        if self._edit_mode:
            self._subname.setReadOnly(True)
            self._rtype.setEnabled(False)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self._validate_and_accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

        self._domain = domain

    def _validate_and_accept(self) -> None:
        if not self._rtype.currentText().strip():
            _err(self, "Record type is required.")
            return
        try:
            int(self._ttl.text().strip())
        except ValueError:
            _err(self, "TTL must be a number.")
            return
        if not self._records.toPlainText().strip():
            _err(self, "At least one record value is required.")
            return
        self.accept()

    @property
    def subname(self) -> str:
        return self._subname.text().strip()

    @property
    def rtype(self) -> str:
        return self._rtype.currentText().strip().upper()

    @property
    def ttl(self) -> int:
        return int(self._ttl.text().strip())

    @property
    def records(self) -> list[str]:
        return [l.strip() for l in self._records.toPlainText().splitlines() if l.strip()]


class AddPolicyDialog(QDialog):
    """
    Form to add an access policy to a token.

    Policies restrict which domains/subnames/record types a token can access.
    Leave a field blank to match anything (wildcard).
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Policy")
        self.resize(380, 200)

        form = QFormLayout(self)

        self._domain = QLineEdit()
        self._domain.setPlaceholderText("example.com (blank = all domains)")
        form.addRow("Domain:", self._domain)

        self._subname = QLineEdit()
        self._subname.setPlaceholderText("@ or www (blank = all subnames)")
        form.addRow("Subname:", self._subname)

        self._rtype = QLineEdit()
        self._rtype.setPlaceholderText("A or TXT (blank = all types)")
        form.addRow("Record type:", self._rtype)

        self._perm_write = QComboBox()
        self._perm_write.addItems(["Read only", "Read + Write"])
        form.addRow("Permission:", self._perm_write)

        btns = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        form.addRow(btns)

    @property
    def domain(self) -> str:
        return self._domain.text().strip()

    @property
    def subname(self) -> str:
        return self._subname.text().strip()

    @property
    def rtype(self) -> str:
        return self._rtype.text().strip().upper()

    @property
    def perm_write(self) -> bool:
        return self._perm_write.currentIndex() == 1


# ─────────────────────────────────────────────────────────────────────────────
# Section 3 — Page widgets
#
# Each "page" is shown in the right pane of the main window.  Pages are
# independent QWidgets so they can be swapped in/out without rebuilding.
# ─────────────────────────────────────────────────────────────────────────────

class _BasePage(QWidget):
    """
    Common base for all page widgets.

    Stores the master token and provides _busy() / _ready() helpers that
    show a "Loading…" label over the table while an API call is in progress.
    """

    def __init__(self, token: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._token = token
        self._workers: list[QThread] = []   # keep references so threads aren't GC'd

    def _keep(self, worker: QThread) -> QThread:
        """Store a worker reference so Python doesn't garbage-collect it mid-run."""
        self._workers.append(worker)
        # Auto-remove from list when done so the list doesn't grow forever
        worker.finished.connect(lambda: self._workers.remove(worker)  # type: ignore[arg-type]
                                if worker in self._workers else None)
        return worker

    def refresh(self) -> None:
        """Override in subclasses to reload data from the API."""
        raise NotImplementedError


class TokensPage(_BasePage):
    """
    Page showing all API tokens for the current master token.

    Toolbar:  [Refresh] [New Token] [Delete]
    Table:    id | name | created | last used | perm_manage_tokens
    """

    def __init__(self, token: str, parent: QWidget | None = None) -> None:
        super().__init__(token, parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── Toolbar ───────────────────────────────────────────────────────────
        bar = QToolBar()
        bar.setMovable(False)

        act_refresh = QAction("⟳ Refresh", self)
        act_refresh.triggered.connect(self.refresh)
        bar.addAction(act_refresh)

        act_new = QAction("＋ New Token", self)
        act_new.triggered.connect(self._create_token)
        bar.addAction(act_new)

        act_del = QAction("✕ Delete", self)
        act_del.triggered.connect(self._delete_token)
        bar.addAction(act_del)

        bar.addSeparator()

        act_policies = QAction("🔒 Policies…", self)
        act_policies.setToolTip("View / manage access policies for the selected token")
        act_policies.triggered.connect(self._open_policies)
        bar.addAction(act_policies)

        layout.addWidget(bar)

        # ── Status label (shown while loading) ───────────────────────────────
        self._status = QLabel("Loading…")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)

        # ── Table ─────────────────────────────────────────────────────────────
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["ID", "Name", "Created", "Last Used", "Manage Tokens"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)

        self.refresh()

    def refresh(self) -> None:
        """Reload the token list from the API."""
        self._status.setText("Loading…")
        self._status.show()
        self._table.hide()

        w = ListTokensWorker(self._token)
        w.result.connect(self._on_tokens)
        w.error.connect(self._on_error)
        self._keep(w).start()

    def _on_tokens(self, tokens: list) -> None:
        self._status.hide()
        self._table.show()
        self._table.setRowCount(0)  # clear existing rows
        for tok in tokens:
            row = self._table.rowCount()
            self._table.insertRow(row)
            # Store the full token dict in the first cell for later retrieval
            item_id = QTableWidgetItem(tok.get("id", ""))
            item_id.setData(Qt.ItemDataRole.UserRole, tok)  # hidden: full dict
            self._table.setItem(row, 0, item_id)
            self._table.setItem(row, 1, QTableWidgetItem(tok.get("name", "") or ""))
            self._table.setItem(row, 2, QTableWidgetItem(
                (tok.get("created") or "")[:19]))     # truncate to seconds
            self._table.setItem(row, 3, QTableWidgetItem(
                (tok.get("last_used") or {}).get("when", "") or "never"))
            self._table.setItem(row, 4, QTableWidgetItem(
                "Yes" if tok.get("perm_manage_tokens") else "No"))

    def _on_error(self, msg: str) -> None:
        self._status.setText(f"Error: {msg}")
        self._status.show()
        self._table.hide()

    def _selected_token_dict(self) -> dict | None:
        """Return the full token dict for the currently selected row, or None."""
        rows = self._table.selectedItems()
        if not rows:
            return None
        # The first column's item carries the full dict
        first_cell = self._table.item(self._table.selectedItems()[0].row(), 0)
        return first_cell.data(Qt.ItemDataRole.UserRole) if first_cell else None

    def _create_token(self) -> None:
        dlg = CreateTokenDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        w = CreateTokenWorker(self._token, dlg.name)
        w.result.connect(self._on_token_created)
        w.error.connect(lambda msg: _err(self, f"Create failed:\n{msg}"))
        self._keep(w).start()

    def _on_token_created(self, tok: dict) -> None:
        # Show the secret once — deSEC never returns it again after this call
        secret = tok.get("token", "(not returned by API)")
        QMessageBox.information(
            self, "Token Created",
            f"<b>Token created.</b><br><br>"
            f"Secret (copy now — never shown again):<br>"
            f"<pre style='background:#222;color:#0f0;padding:8px'>{secret}</pre>"
        )
        self.refresh()

    def _delete_token(self) -> None:
        tok = self._selected_token_dict()
        if not tok:
            _err(self, "Select a token first.")
            return
        name = tok.get("name") or tok.get("id", "?")
        if QMessageBox.question(
            self, "Delete Token",
            f"Delete token <b>{name}</b>?  This cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return

        w = DeleteTokenWorker(self._token, tok["id"])
        w.result.connect(lambda _: self.refresh())
        w.error.connect(lambda msg: _err(self, f"Delete failed:\n{msg}"))
        self._keep(w).start()

    def _open_policies(self) -> None:
        """Open the Policies dialog for the selected token."""
        tok = self._selected_token_dict()
        if not tok:
            _err(self, "Select a token first.")
            return
        dlg = PoliciesDialog(self._token, tok, self)
        dlg.exec()


class PoliciesDialog(QDialog):
    """
    Modal dialog showing the access policies for one specific token.

    Policies are restrictions on which domains/subnames/record types the token
    can read or write.  An empty policy list means the token has no restrictions
    (full access to everything).
    """

    def __init__(self, master: str, token_dict: dict, parent: QWidget) -> None:
        super().__init__(parent)
        self._master     = master
        self._token_dict = token_dict
        self._workers: list[QThread] = []

        name = token_dict.get("name") or token_dict.get("id", "?")
        self.setWindowTitle(f"Policies — {name}")
        self.resize(640, 400)

        layout = QVBoxLayout(self)

        # ── Toolbar ───────────────────────────────────────────────────────────
        bar = QToolBar()
        bar.setMovable(False)

        act_refresh = QAction("⟳ Refresh", self)
        act_refresh.triggered.connect(self._load)
        bar.addAction(act_refresh)

        act_add = QAction("＋ Add", self)
        act_add.triggered.connect(self._add_policy)
        bar.addAction(act_add)

        act_del = QAction("✕ Delete", self)
        act_del.triggered.connect(self._delete_policy)
        bar.addAction(act_del)

        layout.addWidget(bar)

        self._status = QLabel("Loading…")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)

        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(
            ["ID", "Domain", "Subname", "Type", "Write"]
        )
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._load()

    def _keep(self, w: QThread) -> QThread:
        self._workers.append(w)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        return w

    def _load(self) -> None:
        self._status.setText("Loading…")
        self._status.show()
        self._table.hide()
        w = ListPoliciesWorker(self._master, self._token_dict["id"])
        w.result.connect(self._on_policies)
        w.error.connect(self._on_error)
        self._keep(w).start()

    def _on_policies(self, policies: list) -> None:
        self._status.hide()
        self._table.show()
        self._table.setRowCount(0)
        for p in policies:
            row = self._table.rowCount()
            self._table.insertRow(row)
            item_id = QTableWidgetItem(p.get("id", ""))
            item_id.setData(Qt.ItemDataRole.UserRole, p)
            self._table.setItem(row, 0, item_id)
            self._table.setItem(row, 1, QTableWidgetItem(p.get("domain", "") or "*"))
            self._table.setItem(row, 2, QTableWidgetItem(p.get("subname", "") or "*"))
            self._table.setItem(row, 3, QTableWidgetItem(p.get("type", "") or "*"))
            self._table.setItem(row, 4, QTableWidgetItem(
                "Yes" if p.get("perm_write") else "No"))

    def _on_error(self, msg: str) -> None:
        self._status.setText(f"Error: {msg}")
        self._status.show()
        self._table.hide()

    def _selected_policy(self) -> dict | None:
        items = self._table.selectedItems()
        if not items:
            return None
        cell = self._table.item(items[0].row(), 0)
        return cell.data(Qt.ItemDataRole.UserRole) if cell else None

    def _add_policy(self) -> None:
        dlg = AddPolicyDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        w = CreatePolicyWorker(self._master, self._token_dict["id"],
                                dlg.domain, dlg.subname, dlg.rtype, dlg.perm_write)
        w.result.connect(lambda _: self._load())
        w.error.connect(lambda msg: _err(self, f"Add failed:\n{msg}"))
        self._keep(w).start()

    def _delete_policy(self) -> None:
        pol = self._selected_policy()
        if not pol:
            _err(self, "Select a policy first.")
            return
        if QMessageBox.question(
            self, "Delete Policy", "Delete this policy?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        w = DeletePolicyWorker(self._master, self._token_dict["id"], pol["id"])
        w.result.connect(lambda _: self._load())
        w.error.connect(lambda msg: _err(self, f"Delete failed:\n{msg}"))
        self._keep(w).start()


class DomainsPage(_BasePage):
    """
    Page listing all domains.  Click a domain to open its DNS records.

    Toolbar:  [Refresh] [New Domain] [Delete] [View Records…]
    """

    def __init__(self, token: str, parent: QWidget | None = None) -> None:
        super().__init__(token, parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # ── Toolbar ───────────────────────────────────────────────────────────
        bar = QToolBar()
        bar.setMovable(False)

        act_refresh = QAction("⟳ Refresh", self)
        act_refresh.triggered.connect(self.refresh)
        bar.addAction(act_refresh)

        act_new = QAction("＋ New Domain", self)
        act_new.triggered.connect(self._create_domain)
        bar.addAction(act_new)

        act_del = QAction("✕ Delete", self)
        act_del.triggered.connect(self._delete_domain)
        bar.addAction(act_del)

        bar.addSeparator()

        act_records = QAction("DNS Records…", self)
        act_records.setToolTip("View and edit DNS records for the selected domain")
        act_records.triggered.connect(self._open_records)
        bar.addAction(act_records)

        layout.addWidget(bar)

        self._status = QLabel("Loading…")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)

        # ── Table ─────────────────────────────────────────────────────────────
        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Domain", "Created", "Published", "Minimum TTL"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        # Double-click to open records
        self._table.cellDoubleClicked.connect(lambda r, c: self._open_records())
        layout.addWidget(self._table)

        self.refresh()

    def refresh(self) -> None:
        self._status.setText("Loading…")
        self._status.show()
        self._table.hide()
        w = ListDomainsWorker(self._token)
        w.result.connect(self._on_domains)
        w.error.connect(self._on_error)
        self._keep(w).start()

    def _on_domains(self, domains: list) -> None:
        self._status.hide()
        self._table.show()
        self._table.setRowCount(0)
        for d in domains:
            row = self._table.rowCount()
            self._table.insertRow(row)
            self._table.setItem(row, 0, QTableWidgetItem(d.get("name", "")))
            self._table.setItem(row, 1, QTableWidgetItem((d.get("created") or "")[:19]))
            self._table.setItem(row, 2, QTableWidgetItem((d.get("published") or "")[:19]))
            self._table.setItem(row, 3, QTableWidgetItem(str(d.get("minimum_ttl", ""))))

    def _on_error(self, msg: str) -> None:
        self._status.setText(f"Error: {msg}")
        self._status.show()
        self._table.hide()

    def _selected_domain(self) -> str | None:
        items = self._table.selectedItems()
        if not items:
            return None
        return self._table.item(items[0].row(), 0).text()

    def _create_domain(self) -> None:
        name, ok = QInputDialog.getText(self, "New Domain", "Domain name (e.g. example.dedyn.io):")
        if not ok or not name.strip():
            return
        w = CreateDomainWorker(self._token, name.strip())
        w.result.connect(lambda _: self.refresh())
        w.error.connect(lambda msg: _err(self, f"Create failed:\n{msg}"))
        self._keep(w).start()

    def _delete_domain(self) -> None:
        domain = self._selected_domain()
        if not domain:
            _err(self, "Select a domain first.")
            return
        if QMessageBox.question(
            self, "Delete Domain",
            f"Permanently delete <b>{domain}</b> and ALL its DNS records?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        w = DeleteDomainWorker(self._token, domain)
        w.result.connect(lambda _: self.refresh())
        w.error.connect(lambda msg: _err(self, f"Delete failed:\n{msg}"))
        self._keep(w).start()

    def _open_records(self) -> None:
        domain = self._selected_domain()
        if not domain:
            _err(self, "Select a domain first.")
            return
        dlg = RRSetsDialog(self._token, domain, self)
        dlg.exec()


class RRSetsDialog(QDialog):
    """
    Modal dialog showing DNS records (RRsets) for one domain.

    Toolbar:  [Refresh] [Add Record] [Edit] [Delete]
    Table:    subname | type | TTL | records (comma-joined)
    """

    def __init__(self, token: str, domain: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._token   = token
        self._domain  = domain
        self._workers: list[QThread] = []

        self.setWindowTitle(f"Records — {domain}")
        self.resize(720, 500)

        layout = QVBoxLayout(self)

        # ── Toolbar ───────────────────────────────────────────────────────────
        bar = QToolBar()
        bar.setMovable(False)

        for label, slot in [
            ("⟳ Refresh",     self._load),
            ("＋ Add Record", self._add_record),
            ("✎ Edit",        self._edit_record),
            ("✕ Delete",      self._delete_record),
        ]:
            act = QAction(label, self)
            act.triggered.connect(slot)
            bar.addAction(act)

        layout.addWidget(bar)

        self._status = QLabel("Loading…")
        self._status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["Subname", "Type", "TTL", "Records"])
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.cellDoubleClicked.connect(lambda r, c: self._edit_record())
        layout.addWidget(self._table)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

        self._load()

    def _keep(self, w: QThread) -> QThread:
        self._workers.append(w)
        w.finished.connect(lambda: self._workers.remove(w) if w in self._workers else None)
        return w

    def _load(self) -> None:
        self._status.setText("Loading…")
        self._status.show()
        self._table.hide()
        w = ListRRSetsWorker(self._token, self._domain)
        w.result.connect(self._on_rrsets)
        w.error.connect(self._on_error)
        self._keep(w).start()

    def _on_rrsets(self, rrsets: list) -> None:
        self._status.hide()
        self._table.show()
        self._table.setRowCount(0)
        for rr in rrsets:
            row = self._table.rowCount()
            self._table.insertRow(row)
            item = QTableWidgetItem(rr.get("subname", ""))
            item.setData(Qt.ItemDataRole.UserRole, rr)  # store full dict
            self._table.setItem(row, 0, item)
            self._table.setItem(row, 1, QTableWidgetItem(rr.get("type", "")))
            self._table.setItem(row, 2, QTableWidgetItem(str(rr.get("ttl", ""))))
            self._table.setItem(row, 3, QTableWidgetItem(
                ", ".join(rr.get("records", []))))

    def _on_error(self, msg: str) -> None:
        self._status.setText(f"Error: {msg}")
        self._status.show()
        self._table.hide()

    def _selected_rrset(self) -> dict | None:
        items = self._table.selectedItems()
        if not items:
            return None
        cell = self._table.item(items[0].row(), 0)
        return cell.data(Qt.ItemDataRole.UserRole) if cell else None

    def _add_record(self) -> None:
        dlg = AddRRSetDialog(self, self._domain)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_record(dlg, existing=None)

    def _edit_record(self) -> None:
        rr = self._selected_rrset()
        if not rr:
            _err(self, "Select a record first.")
            return
        dlg = AddRRSetDialog(self, self._domain, existing=rr)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_record(dlg, existing=rr)

    def _save_record(self, dlg: AddRRSetDialog, existing: dict | None) -> None:
        w = UpsertRRSetWorker(
            self._token, self._domain,
            dlg.subname, dlg.rtype, dlg.ttl, dlg.records
        )
        w.result.connect(lambda _: self._load())
        w.error.connect(lambda msg: _err(self, f"Save failed:\n{msg}"))
        self._keep(w).start()

    def _delete_record(self) -> None:
        rr = self._selected_rrset()
        if not rr:
            _err(self, "Select a record first.")
            return
        subname = rr.get("subname", "@") or "@"
        rtype   = rr.get("type", "?")
        if QMessageBox.question(
            self, "Delete Record",
            f"Delete <b>{subname} {rtype}</b>?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        ) != QMessageBox.StandardButton.Yes:
            return
        w = DeleteRRSetWorker(self._token, self._domain,
                               rr.get("subname", ""), rr.get("type", ""))
        w.result.connect(lambda _: self._load())
        w.error.connect(lambda msg: _err(self, f"Delete failed:\n{msg}"))
        self._keep(w).start()


# ─────────────────────────────────────────────────────────────────────────────
# Section 4 — Main window
# ─────────────────────────────────────────────────────────────────────────────

class MainWindow(QMainWindow):
    """
    Top-level application window.

    Layout:
      ┌──────────┬───────────────────────────────────────┐
      │  Nav     │  Content (QStackedWidget)              │
      │  list    │                                        │
      │  Tokens  │  (TokensPage / DomainsPage)            │
      │  Domains │                                        │
      └──────────┴───────────────────────────────────────┘

    The left nav is a QListWidget.  Selecting an item switches the right
    pane via QStackedWidget.setCurrentIndex().
    """

    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token
        self.setWindowTitle("deSEC Manager")
        self.resize(1000, 620)

        # ── Status bar ────────────────────────────────────────────────────────
        self.statusBar().showMessage("Connected to deSEC API")

        # ── Central layout ────────────────────────────────────────────────────
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        # ── Left navigation list ──────────────────────────────────────────────
        self._nav = QListWidget()
        self._nav.setFixedWidth(160)
        self._nav.setFont(QFont("system-ui", 12))

        nav_items = [
            ("🔑  Tokens",   "Manage API tokens and their policies"),
            ("🌐  Domains",  "Manage domains and DNS records"),
        ]
        for label, tip in nav_items:
            item = QListWidgetItem(label)
            item.setToolTip(tip)
            self._nav.addItem(item)

        splitter.addWidget(self._nav)

        # ── Right stacked content ─────────────────────────────────────────────
        self._stack = QStackedWidget()
        self._tokens_page  = TokensPage(token)
        self._domains_page = DomainsPage(token)
        self._stack.addWidget(self._tokens_page)
        self._stack.addWidget(self._domains_page)
        splitter.addWidget(self._stack)

        splitter.setStretchFactor(0, 0)  # nav: fixed
        splitter.setStretchFactor(1, 1)  # content: expands

        # Connect nav selection to page switching
        self._nav.currentRowChanged.connect(self._stack.setCurrentIndex)
        self._nav.setCurrentRow(0)  # start on Tokens page

        # ── Menu bar ──────────────────────────────────────────────────────────
        file_menu = self.menuBar().addMenu("File")

        act_refresh = QAction("Refresh current page", self)
        act_refresh.setShortcut("F5")
        act_refresh.triggered.connect(self._refresh_current)
        file_menu.addAction(act_refresh)

        file_menu.addSeparator()

        act_quit = QAction("Quit", self)
        act_quit.setShortcut("Ctrl+Q")
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        view_menu = self.menuBar().addMenu("View")

        act_tokens = QAction("Tokens", self)
        act_tokens.triggered.connect(lambda: self._nav.setCurrentRow(0))
        view_menu.addAction(act_tokens)

        act_domains = QAction("Domains", self)
        act_domains.triggered.connect(lambda: self._nav.setCurrentRow(1))
        view_menu.addAction(act_domains)

    def _refresh_current(self) -> None:
        """Refresh the page that is currently visible."""
        page = self._stack.currentWidget()
        if hasattr(page, "refresh"):
            page.refresh()


# ─────────────────────────────────────────────────────────────────────────────
# Section 5 — Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    """
    Launch the deSEC Qt GUI.

    Called by desec.py when the user passes --gui.

    Steps:
      1. Create the QApplication (Qt's global root object)
      2. Load any saved token from desec.env
      3. Show LoginDialog if no token is saved or if the saved token is stale
      4. Create and show the MainWindow
      5. Enter Qt's event loop (blocks until the window is closed)
    """

    # QApplication must be created before any widgets.
    # sys.argv is passed so Qt can parse its own command-line flags.
    app = QApplication(sys.argv)

    # Apply a dark-ish style so it looks reasonable on all platforms
    app.setStyle("Fusion")

    # Load saved token (may be empty string if not configured yet)
    env = load_env()
    saved_token = env.get("DESEC_TOKEN", "").strip()

    token: str = ""

    if saved_token:
        # We have a saved token — try it silently before showing any dialog.
        # This avoids an unnecessary prompt on every launch when the token is
        # still valid.  If the API call succeeds, skip the login dialog entirely.
        try:
            list_tokens(saved_token)
            token = saved_token   # token is good, proceed directly to main window
        except Exception:
            # Token is stale / invalid — fall through to show the login dialog
            pass

    if not token:
        # No saved token, or the saved one failed — show the login dialog so
        # the user can enter / correct their token.
        dlg = LoginDialog(existing_token=saved_token)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            # User clicked Cancel — nothing to do, exit cleanly
            sys.exit(0)
        token = dlg.token

    # Build and show the main window
    window = MainWindow(token)
    window.show()

    # Enter Qt's event loop.  This call blocks until the user closes the window.
    # sys.exit() propagates Qt's exit code back to the shell.
    sys.exit(app.exec())
