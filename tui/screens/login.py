# tui/screens/login.py
#
# LoginScreen — the first screen the user sees when launching the TUI.
#
# WHAT IT DOES:
#   1. Shows a centered login form with a token input field.
#   2. If a token is already saved in desec.env, it pre-fills the field
#      and auto-connects immediately without requiring a button click.
#   3. On "Connect", it calls list_tokens() with the entered token to verify
#      it works.  If successful: saves the token to desec.env and navigates
#      to TokenListScreen.  If failed: shows an error message in red.
#
# WHY WE VERIFY WITH list_tokens():
#   There's no separate "validate token" endpoint in deSEC.  Calling list_tokens()
#   is the lightest operation that requires auth — it'll return 401 if the token
#   is wrong and 200 if correct.
#
# IF THE LOGIN SCREEN WON'T CONNECT:
#   - Check your internet connection
#   - Verify the token in ~/.config/mech-goodies/desec.env
#   - If DESEC_API_BASE is set to something custom, verify that URL is reachable
#   - The error label at the bottom will show the HTTP status code

from __future__ import annotations

import asyncio              # for run_in_executor (runs blocking HTTP calls off the main thread)
from typing import TYPE_CHECKING

import httpx                # HTTP client — used to detect auth errors specifically

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label
from textual import on, work

from core.env import load_env, save_env, ENV_PATH
from core.api import list_tokens

# TYPE_CHECKING guard: DeSECApp is only imported when a type checker runs (not at runtime).
# This avoids circular imports — tui/screens/login.py → tui/app.py → tui/screens/login.py
if TYPE_CHECKING:
    from tui.app import DeSECApp


class LoginScreen(Screen):
    """
    The initial authentication screen.

    Prompts the user for their deSEC master API token.  On success, stores the
    token in desec.env and navigates to TokenListScreen.

    The TYPE_CHECKING guard on the `app` property is a Textual pattern for
    giving the type checker a more specific type for self.app than the generic
    App class.  At runtime, `self.app` is always the actual DeSECApp instance.
    """

    if TYPE_CHECKING:
        @property
        def app(self) -> DeSECApp: ...   # type: ignore[override]

    def compose(self) -> ComposeResult:
        """
        Build the login form layout.

        The outer Container (login-center) is full-screen with centered alignment.
        The inner Container (login-container) is a fixed-width box with a border.
        """
        # Load any saved token so we can pre-fill the input
        saved = load_env().get("DESEC_TOKEN", "")

        # Show different hint text depending on whether a token is already saved
        hint = (
            "[dim]Token found in desec.env — connecting…[/]"
            if saved
            else "Enter your deSEC API token to continue:"
        )

        yield Header()
        with Container(id="login-center"):
            with Container(id="login-container"):
                yield Label("[bold cyan]deSEC Token Manager[/]", id="login-title")
                # Show the config file path so the user knows where to edit it manually
                yield Label(f"[dim]{ENV_PATH}[/]", id="login-env-path")
                yield Label(hint, id="login-hint")
                # password=True hides the token characters as the user types
                yield Input(
                    password=True,
                    placeholder="Paste token here…",
                    value=saved,
                    id="token-input",
                )
                yield Button("Connect", id="connect-btn", variant="primary")
                # Error label starts empty; _try_connect() fills it on failure
                yield Label("", id="login-error")
        yield Footer()

    def on_mount(self) -> None:
        """
        Called by Textual after the screen is rendered.

        If a saved token was found, auto-connect immediately.
        Otherwise, focus the input field so the user can start typing right away.
        """
        if load_env().get("DESEC_TOKEN"):
            # Auto-connect: skip the button click if we already have a token
            self._try_connect(load_env()["DESEC_TOKEN"])
        else:
            # Focus the input so the user can start typing without clicking first
            self.query_one("#token-input", Input).focus()

    @on(Button.Pressed, "#connect-btn")
    @on(Input.Submitted, "#token-input")  # also triggered when user presses Enter in the input
    def do_login(self) -> None:
        """
        Called when the user presses Connect or hits Enter in the token field.
        Reads the input value and starts the connection attempt.
        """
        token = self.query_one("#token-input", Input).value.strip()
        if not token:
            return  # Don't attempt to connect with an empty token
        self._try_connect(token)

    @work(exclusive=True)
    async def _try_connect(self, token: str) -> None:
        """
        Validate the token by calling list_tokens() and navigate on success.

        @work(exclusive=True) means Textual runs this in a background worker.
        Only one instance can run at a time — clicking Connect twice won't
        launch two simultaneous requests.

        We use run_in_executor() because list_tokens() is a blocking (synchronous)
        function that makes an HTTP request.  Running it directly in an async
        function would freeze the entire TUI.  run_in_executor() offloads it to
        a thread pool so the TUI stays responsive.

        Parameters:
          token — the API token string to validate
        """
        err_label = self.query_one("#login-error", Label)
        try:
            # Run the blocking HTTP call in a thread pool executor
            await asyncio.get_event_loop().run_in_executor(None, list_tokens, token)

            # If we get here, the token is valid
            self.app.master_token = token   # store token on the app for other screens to use

            # Persist the token so we auto-connect next time
            save_env({"DESEC_TOKEN": token})

            # Navigate to the main token list screen
            # Import here to avoid circular import at module load time
            from tui.screens.tokens import TokenListScreen
            self.app.push_screen(TokenListScreen())

        except httpx.HTTPStatusError as e:
            # The server responded with an error status code
            # 401 = wrong token, 403 = insufficient permissions
            err_label.update(f"[red]Auth failed: {e.response.status_code}[/]")
        except Exception as e:
            # Any other error (network down, DNS failure, etc.)
            err_label.update(f"[red]Error: {e}[/]")
