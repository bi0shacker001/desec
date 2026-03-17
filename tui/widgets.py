# tui/widgets.py
#
# Reusable modal dialog widgets for the desec TUI.
#
# A "ModalScreen" in Textual is a dialog that floats on top of the current
# screen.  It blocks interaction with the screen behind it until dismissed.
# The user must close the modal before they can interact with anything else.
#
# WIDGETS IN THIS FILE:
#   MessageModal    — A simple OK popup for info or error messages
#   ConfirmModal    — A Yes/No confirmation dialog; returns bool to caller
#   NewSecretModal  — Displays a one-time token secret; prompts user to copy it
#
# HOW MODALS WORK IN TEXTUAL:
#   1. Caller pushes the modal:  self.app.push_screen(MessageModal("Title", "body"))
#   2. When user dismisses it:   self.dismiss()  or  self.dismiss(return_value)
#   3. Caller receives the value if they provided a callback:
#      self.app.push_screen(ConfirmModal("Sure?"), my_callback)
#      def my_callback(ok: bool | None) -> None: ...
#
# IF A MODAL WON'T CLOSE:
#   Make sure the escape/enter binding calls self.dismiss() or self.app.pop_screen().
#   If the screen is stuck, press Ctrl+C to exit the TUI entirely.

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static
from textual import on


# ──────────────────────────────────────────────────────────────────────────────
# MessageModal
# ──────────────────────────────────────────────────────────────────────────────

class MessageModal(ModalScreen):
    """
    A simple one-button popup for displaying information or error messages.

    Usage:
      self.app.push_screen(MessageModal("Success", "Token was deleted."))
      self.app.push_screen(MessageModal("Error", "API returned 403.", is_error=True))

    The user dismisses it by pressing OK, Enter, Space, or Escape.
    No return value is produced (the caller doesn't need to know when it closed).
    """

    # Keyboard shortcuts that dismiss this modal.
    # Multiple keys are separated by commas in Textual binding syntax.
    BINDINGS = [Binding("escape,enter,space", "dismiss", "Close")]

    def __init__(self, title: str, message: str, is_error: bool = False):
        """
        Parameters:
          title    — bold heading shown at the top of the dialog
          message  — body text (supports Textual markup like [bold]text[/])
          is_error — if True, the title is shown in red; otherwise green
        """
        super().__init__()
        self._title = title
        self._message = message
        self._is_error = is_error

    def compose(self) -> ComposeResult:
        """
        Build the widget tree for this modal.
        compose() is called by Textual when the screen is mounted.
        `yield` each widget in the order they should appear.
        """
        # Red title for errors, green for success/info
        colour = "red" if self._is_error else "green"
        with Container(id="modal-box"):
            # [bold red]Title[/] uses Textual's Rich markup for coloured text
            yield Label(f"[bold {colour}]{self._title}[/]", id="modal-title")
            yield Static(self._message, id="modal-body")
            yield Button("OK", id="ok-btn", variant="primary")

    @on(Button.Pressed, "#ok-btn")
    def close(self) -> None:
        """Called when the OK button is pressed — dismiss closes this modal."""
        self.dismiss()


# ──────────────────────────────────────────────────────────────────────────────
# ConfirmModal
# ──────────────────────────────────────────────────────────────────────────────

class ConfirmModal(ModalScreen[bool]):
    """
    A Yes/No confirmation dialog.  Returns True (Yes) or False (No) to the caller.

    Usage:
      def _on_confirm(ok: bool | None) -> None:
          if ok:
              self._do_delete(...)
      self.app.push_screen(ConfirmModal("Delete this? This cannot be undone."), _on_confirm)

    The type parameter ModalScreen[bool] tells Textual what type dismiss() returns.
    The caller's callback receives True if Yes was clicked, False otherwise.
    """

    # Escape key is treated as "No" / cancel
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, message: str):
        """
        Parameters:
          message — the question/warning text to display to the user
        """
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        """Build the Yes/No dialog layout."""
        with Container(id="modal-box"):
            yield Label("[bold yellow]Confirm[/]", id="modal-title")
            yield Static(self._message, id="modal-body")
            with Horizontal(id="modal-buttons"):
                # Error variant makes Yes button red — visually warns the user
                yield Button("Yes", id="yes-btn", variant="error")
                yield Button("No", id="no-btn", variant="primary")

    @on(Button.Pressed, "#yes-btn")
    def yes(self) -> None:
        """User clicked Yes — dismiss with True."""
        self.dismiss(True)

    @on(Button.Pressed, "#no-btn")
    def no(self) -> None:
        """User clicked No — dismiss with False."""
        self.dismiss(False)

    def action_cancel(self) -> None:
        """Escape key pressed — treat as No."""
        self.dismiss(False)


# ──────────────────────────────────────────────────────────────────────────────
# NewSecretModal
# ──────────────────────────────────────────────────────────────────────────────

class NewSecretModal(ModalScreen):
    """
    Displays a newly created token's secret value.

    IMPORTANT: deSEC only returns the secret token value ONCE, at creation time.
    It cannot be retrieved again.  This modal exists specifically to show the
    user that value in a prominent way and remind them to copy it before closing.

    Usage:
      result = create_token(...)            # returns dict with "token" key
      secret = result.get("token", "")
      self.app.push_screen(NewSecretModal(token_name, secret))
    """

    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def __init__(self, token_name: str, secret: str):
        """
        Parameters:
          token_name — display name of the token (shown for context)
          secret     — the one-time secret value to display
        """
        super().__init__()
        self._name = token_name
        self._secret = secret

    def compose(self) -> ComposeResult:
        """Build the secret display dialog."""
        with Container(id="modal-box"):
            yield Label("[bold green]Token created![/]", id="modal-title")
            yield Static(
                # Textual markup: [bold] = bold, [red] = red text, [yellow] = yellow
                # The secret is highlighted in yellow so it stands out visually
                f"[bold]Name:[/] {self._name}\n\n"
                f"[bold red]Secret (shown once — copy it now):[/]\n\n"
                f"[bold yellow]{self._secret}[/]",
                id="modal-body",
            )
            yield Button("I've copied it", id="ok-btn", variant="primary")

    @on(Button.Pressed, "#ok-btn")
    def close(self) -> None:
        """User confirms they've copied the secret — close the dialog."""
        self.dismiss()
