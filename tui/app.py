# tui/app.py
#
# The root Textual application class and CSS for the desec TUI.
#
# DeSECApp is the top-level Textual App object.  It:
#   - Holds shared state (master_token, _tokens list) accessible from all screens
#   - Defines all CSS styling for every screen and widget
#   - Launches LoginScreen on startup
#
# Textual 8.x GOTCHAS (documented here so future maintainers know why the CSS is written this way):
#
#   1. Input widgets are nearly invisible in the default dark theme because
#      $border-blurred (#191919) is almost the same colour as $surface (#1E1E1E).
#      FIX: Override Input's border in App-level CSS to a visible gray:
#        Input { border: tall $foreground-darken-3; }
#      AND re-declare the focused border (App CSS overrides DEFAULT_CSS for all states,
#      wiping out the blue focus ring unless you explicitly restore it):
#        Input:focus { border: tall $border; }
#
#   2. ScrollableContainer with height: 1fr inside a height: auto parent collapses
#      children to 0 visible rows.  Input borders render but text is invisible.
#      FIX: Use height: auto + max-height: N on ScrollableContainer instead of 1fr.
#
#   3. $text is "auto 87%" (theme-relative), which can produce unreadable contrast.
#      FIX: Use $foreground (#E0E0E0 explicit) for Input text color.
#
# HOW TEXTUAL CSS WORKS:
#   The CSS string here uses Textual's TCSS (Textual CSS) syntax, which is similar
#   to standard CSS but has Textual-specific selectors and variables.
#   $primary, $panel, $error etc. are theme variables defined by Textual.
#   Widgets are selected by ID (#login-center) or type (Input, Button, etc.).
#   Multiple classes can be used like: Container.wide { ... }

from __future__ import annotations

from textual.app import App
from textual.binding import Binding


# ──────────────────────────────────────────────────────────────────────────────
# CSS
# ──────────────────────────────────────────────────────────────────────────────

# This is the complete stylesheet for the entire TUI.  It controls layout and
# appearance for all screens and widgets.  Keep this in one place so it's easy
# to find and edit.  Individual screens use the IDs defined here to position
# their widgets.

CSS = """
/* ── Global ────────────────────────────────────────────────────────────────── */
Screen { background: $surface; }
/* "hidden" class: used to show/hide widgets dynamically (e.g. CNAME quick-pick) */
.hidden { display: none; }

/* ── Input visibility fix (Textual 8 dark theme) ───────────────────────────── */
/* Unfocused input border is nearly invisible by default — force a visible gray. */
/* App-level CSS overrides DEFAULT_CSS, so we MUST also re-declare Input:focus   */
/* here to restore the blue focused border that Textual normally provides.        */
Input {
    color: $foreground;
    border: tall $foreground-darken-3;
}
Input:focus {
    border: tall $border;
}

/* ── Login screen ────────────────────────────────────────────────────────────── */
#login-center {
    width: 100%;
    height: 1fr;
    align: center middle;     /* center the login box horizontally AND vertically */
}
#login-container {
    width: 60;
    max-width: 95%;
    height: auto;
    padding: 2 3;
    border: round $primary;
    background: $panel;
}
#login-title     { text-align: center; margin-bottom: 1; }
#login-env-path  { text-align: center; color: $text-muted; margin-bottom: 1; }
#login-error     { color: $error; margin-top: 1; }
#login-container Input { margin-bottom: 1; }

/* ── Token list screen ────────────────────────────────────────────────────────── */
#token-screen { padding: 1 2; }
#token-title  { margin-bottom: 1; }
#token-actions { height: auto; margin-top: 1; }
#token-actions Button { margin-right: 1; }

/* ── Policy screen ────────────────────────────────────────────────────────────── */
#policy-screen { padding: 1 2; }
#policy-title  { margin-bottom: 1; }
#policy-hint   { color: $text-muted; margin-bottom: 1; }
#policy-actions { height: auto; margin-top: 1; }
#policy-actions Button { margin-right: 1; }

/* ── Domain list screen ────────────────────────────────────────────────────────── */
#domain-screen { padding: 1 2; }
#domain-title  { margin-bottom: 1; }
#domain-actions { height: auto; margin-top: 1; }
#domain-actions Button { margin-right: 1; }

/* ── RRset screen ────────────────────────────────────────────────────────────── */
#rrset-screen  { padding: 1 2; }
#rrset-title   { margin-bottom: 1; }
#rrset-actions { height: auto; margin-top: 1; }
#rrset-actions Button { margin-right: 1; }

/* ── Modals (shared) ────────────────────────────────────────────────────────── */
/* ModalScreen has a semi-transparent background overlay that dims the screen behind it */
ModalScreen {
    align: center middle;
    background: $background 60%;   /* 60% opacity darkens background without hiding it */
}
#modal-box {
    width: 60;
    max-width: 95%;
    height: auto;
    max-height: 80vh;   /* prevent modals from overflowing tiny terminals */
    padding: 2 3;
    border: round $accent;
    background: $panel;
}
#modal-box.wide { width: 80; max-width: 95%; }   /* wider modals for complex forms */
#modal-title { margin-bottom: 1; }
#modal-body  { margin-bottom: 1; }
#modal-buttons { height: auto; margin-top: 1; }
#modal-buttons Button { margin-right: 1; }

/* Input/Select/Label/Checkbox spacing inside modals */
ModalScreen Input,
ModalScreen Select    { margin-bottom: 1; color: $foreground; }
ModalScreen Label     { margin-top: 1; }
ModalScreen Checkbox  { margin-bottom: 1; }
ModalScreen TextArea  { height: 8; margin-bottom: 1; }

/* IMPORTANT: Use height: auto (not 1fr) for ScrollableContainer inside auto-height parents.
   Using 1fr here would collapse all children to 0 height in Textual 8, making Input text
   invisible even though the border still renders.  max-height: 40 allows scrolling for
   tall forms without overflowing the screen. */
ScrollableContainer { height: auto; max-height: 40; }

/* ── CertMultiScreen ────────────────────────────────────────────────────────── */
#certmulti-screen  { padding: 1 2; }
#certmulti-title   { margin-bottom: 1; }
#certmulti-table   { height: 1fr; max-height: 16; margin-bottom: 1; }
#certmulti-add-row { height: auto; margin-bottom: 1; }
#certmulti-add-row Select { width: 1fr; margin-right: 1; }
#certmulti-add-row Input  { width: 1fr; margin-right: 1; }
#certmulti-add-row Button { width: auto; }
#certmulti-screen Label   { margin-top: 1; margin-bottom: 0; }
#certmulti-screen Input   { margin-bottom: 1; }
#certmulti-actions { height: auto; margin-top: 1; }
#certmulti-actions Button { margin-right: 1; }
"""


# ──────────────────────────────────────────────────────────────────────────────
# DeSECApp
# ──────────────────────────────────────────────────────────────────────────────

class DeSECApp(App):
    """
    The root Textual application for the deSEC manager TUI.

    This class:
      1. Sets the window title shown in the terminal header
      2. Applies the CSS stylesheet
      3. Holds shared state (master_token, _tokens) accessible from all screens
         via self.app.master_token, self.app._tokens
      4. Launches the LoginScreen as the first thing shown

    HOW TO ADD A NEW SCREEN:
      Import your screen class in the file that opens it, then call:
        self.app.push_screen(YourScreen())   ← to navigate to it
        self.app.pop_screen()                ← to go back

    HOW TO ADD SHARED STATE:
      Add a class-level attribute here (like master_token and _tokens).
      All screens can then read/write it via self.app.<attribute>.
    """

    TITLE = "deSEC Token Manager"
    CSS = CSS  # apply the stylesheet defined above

    # Ctrl+C quits the app.  show=False means it doesn't appear in the footer.
    BINDINGS = [Binding("ctrl+c", "quit", "Quit", show=False)]

    # Shared state: the master API token, set by LoginScreen on successful login.
    # All other screens read this to make authenticated API calls.
    master_token: str = ""

    # Shared state: the last-fetched list of tokens, stored here so TokenListScreen
    # can look up a token by row index without re-fetching.
    _tokens: list = []

    def on_mount(self) -> None:
        """
        Called by Textual after the app starts.

        ensure_env_complete() checks whether the config file is missing any
        known settings and adds commented-out stubs if so.  This is safe to
        call on every startup — it only appends, never overwrites.

        Then we push the LoginScreen which is the first thing the user sees.
        """
        from core.env import ensure_env_complete
        from tui.screens.login import LoginScreen

        ensure_env_complete()
        self.push_screen(LoginScreen())
