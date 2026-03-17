"""
programs/desec/main.py — Flet GUI frontend for deSEC DNS management.

This is a thin UI wrapper around python-scripts/desec/desec-api.py.
All API logic lives in that script; this file calls it via subprocess
and displays the results.  The desec-api.py script is never modified.

Usage:
  python main.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import flet as ft

# ── Paths ──────────────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent.parent
SCRIPT = _REPO_ROOT / "python-scripts" / "desec" / "desec-api.py"
ENV_PATH = Path.home() / ".config" / "mech-goodies" / "desec.env"

# ── Config helpers ─────────────────────────────────────────────────────────────

def _load_env() -> dict[str, str]:
    """Read ~/.config/mech-goodies/desec.env (KEY=VALUE pairs)."""
    if not ENV_PATH.exists():
        return {}
    out: dict[str, str] = {}
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip()
    return out


def _save_env(data: dict[str, str]) -> None:
    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{k}={v}" for k, v in data.items()]
    ENV_PATH.write_text("\n".join(lines) + "\n")


def _load_token() -> str:
    return _load_env().get("DESEC_TOKEN", "")


def _save_token(token: str) -> None:
    env = _load_env()
    env["DESEC_TOKEN"] = token
    _save_env(env)


# ── Subprocess helper ──────────────────────────────────────────────────────────

def run_desec(*args: str, token: str = "") -> Any:
    """
    Call desec-api.py with the given CLI args and return parsed JSON.
    Raises RuntimeError on non-zero exit.
    """
    env = os.environ.copy()
    if token:
        env["DESEC_TOKEN"] = token
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output", "json", *args],
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        msg = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(msg or "desec-api.py returned non-zero exit code")
    raw = result.stdout.strip()
    if not raw:
        return []
    return json.loads(raw)


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _loading_spinner() -> ft.Control:
    return ft.Row(
        [ft.ProgressRing(width=20, height=20), ft.Text("Loading…")],
        alignment=ft.MainAxisAlignment.CENTER,
    )


def _error_row(msg: str) -> ft.Control:
    return ft.Row(
        [ft.Icon(ft.Icons.ERROR_OUTLINE, color=ft.Colors.RED_400),
         ft.Text(msg, color=ft.Colors.RED_400, expand=True)],
    )


# ── App class ──────────────────────────────────────────────────────────────────

class DeSECApp:
    """Root application — manages page and view stack."""

    def __init__(self, page: ft.Page) -> None:
        self.page = page
        self._token: str = _load_token()
        self._nav_rail: ft.NavigationRail | None = None
        self._content = ft.Container(expand=True)
        self._views: dict[str, ft.Control] = {}

        page.title = "deSEC Manager"
        page.theme_mode = ft.ThemeMode.DARK
        page.theme = ft.Theme(color_scheme_seed=ft.Colors.TEAL)
        page.padding = 0

        self._build()

    # ── Build ──────────────────────────────────────────────────────────────────

    def _build(self) -> None:
        if not self._token:
            self.page.controls.clear()
            self.page.controls.append(LoginView(self).build())
            self.page.update()
            return

        self._nav_rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=80,
            destinations=[
                ft.NavigationRailDestination(
                    icon=ft.Icons.VPN_KEY_OUTLINED,
                    selected_icon=ft.Icons.VPN_KEY,
                    label="Tokens",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.LANGUAGE_OUTLINED,
                    selected_icon=ft.Icons.LANGUAGE,
                    label="Domains",
                ),
                ft.NavigationRailDestination(
                    icon=ft.Icons.SHIELD_OUTLINED,
                    selected_icon=ft.Icons.SHIELD,
                    label="Provisioning",
                ),
            ],
            on_change=self._on_nav,
        )

        layout = ft.Row(
            [
                self._nav_rail,
                ft.VerticalDivider(width=1),
                self._content,
            ],
            expand=True,
            spacing=0,
        )

        top_bar = ft.Container(
            content=ft.Row(
                [
                    ft.Icon(ft.Icons.DNS, color=ft.Colors.TEAL_400),
                    ft.Text("deSEC Manager", size=16, weight=ft.FontWeight.W_600, expand=True),
                    ft.TextButton(
                        "Switch token",
                        icon=ft.Icons.SWAP_HORIZ,
                        on_click=self._on_switch_token,
                    ),
                ],
            ),
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            padding=ft.padding.symmetric(horizontal=16, vertical=8),
        )

        self.page.controls.clear()
        self.page.controls.append(
            ft.Column([top_bar, layout], expand=True, spacing=0)
        )
        self.page.update()
        self._navigate(0)

    def _on_nav(self, e: ft.ControlEvent) -> None:
        self._navigate(int(e.data))

    def _navigate(self, idx: int) -> None:
        view_classes = [TokenListView, DomainListView, ProvisioningView]
        key = f"view_{idx}"
        if key not in self._views:
            self._views[key] = view_classes[idx](self).build()
        self._content.content = self._views[key]
        self._content.update()

    def _on_switch_token(self, _: Any) -> None:
        self._token = ""
        self._views.clear()
        self._build()

    # ── Token access ───────────────────────────────────────────────────────────

    def set_token(self, token: str, save: bool = True) -> None:
        self._token = token
        if save:
            _save_token(token)
        self._views.clear()
        self._build()

    def run(self, *args: str) -> Any:
        return run_desec(*args, token=self._token)

    def run_bg(
        self,
        args: tuple[str, ...],
        on_result: "callable[[Any], None]",
        on_error: "callable[[str], None]",
    ) -> None:
        """Run a desec command in a background thread."""
        def _worker() -> None:
            try:
                result = self.run(*args)
                self.page.run_thread(lambda: on_result(result))
            except Exception as exc:
                self.page.run_thread(lambda: on_error(str(exc)))
        threading.Thread(target=_worker, daemon=True).start()

    def refresh_view(self, idx: int) -> None:
        key = f"view_{idx}"
        self._views.pop(key, None)
        if self._nav_rail and self._nav_rail.selected_index == idx:
            self._navigate(idx)


# ── Login view ────────────────────────────────────────────────────────────────

class LoginView:
    def __init__(self, app: DeSECApp) -> None:
        self.app = app

    def build(self) -> ft.Control:
        self._token_field = ft.TextField(
            label="deSEC API token",
            password=True,
            can_reveal_password=True,
            autofocus=True,
            expand=True,
            on_submit=self._on_connect,
        )
        self._status = ft.Text("", color=ft.Colors.RED_400)
        self._busy = ft.ProgressBar(visible=False)
        self._save_cb = ft.Checkbox(label="Save token to desec.env", value=True)

        return ft.Container(
            content=ft.Column(
                [
                    ft.Icon(ft.Icons.DNS, size=48, color=ft.Colors.TEAL_400),
                    ft.Text("deSEC Manager", size=22, weight=ft.FontWeight.W_600),
                    ft.Text("Enter your deSEC API token to continue.", size=13,
                            color=ft.Colors.ON_SURFACE_VARIANT),
                    ft.Container(height=8),
                    self._token_field,
                    ft.Row([self._save_cb]),
                    self._busy,
                    self._status,
                    ft.ElevatedButton("Connect", icon=ft.Icons.LOGIN,
                                      on_click=self._on_connect),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                width=420,
                spacing=10,
            ),
            expand=True,
            alignment=ft.alignment.center,
        )

    def _on_connect(self, _: Any) -> None:
        token = self._token_field.value.strip()
        if not token:
            self._status.value = "Token cannot be empty."
            self._status.update()
            return

        self._busy.visible = True
        self._status.value = ""
        self._busy.update()
        self._status.update()

        def _test() -> None:
            try:
                run_desec("token", "list", token=token)
                save = self._save_cb.value or False
                self.app.page.run_thread(lambda: self.app.set_token(token, save=save))
            except Exception as exc:
                self.app.page.run_thread(lambda: self._show_error(str(exc)))

        threading.Thread(target=_test, daemon=True).start()

    def _show_error(self, msg: str) -> None:
        self._busy.visible = False
        self._status.value = f"Error: {msg}"
        self._busy.update()
        self._status.update()


# ── Token list view ───────────────────────────────────────────────────────────

class TokenListView:
    def __init__(self, app: DeSECApp) -> None:
        self.app = app

    def build(self) -> ft.Control:
        self._body = ft.Column([_loading_spinner()], expand=True, scroll=ft.ScrollMode.AUTO)
        self.app.run_bg(
            ("token", "list"),
            on_result=self._show_tokens,
            on_error=self._show_error,
        )
        return ft.Column(
            [
                ft.Container(
                    content=ft.Row(
                        [
                            ft.Text("API Tokens", size=18, weight=ft.FontWeight.W_600, expand=True),
                            ft.ElevatedButton("New token", icon=ft.Icons.ADD,
                                              on_click=self._on_new_token),
                        ],
                    ),
                    padding=ft.padding.symmetric(horizontal=16, vertical=12),
                ),
                ft.Divider(height=1),
                ft.Container(content=self._body, expand=True, padding=16),
            ],
            expand=True,
            spacing=0,
        )

    def _show_tokens(self, data: Any) -> None:
        if not isinstance(data, list):
            self._body.controls = [_error_row("Unexpected response format")]
            self._body.update()
            return

        if not data:
            self._body.controls = [ft.Text("No tokens found.", italic=True,
                                           color=ft.Colors.ON_SURFACE_VARIANT)]
            self._body.update()
            return

        rows: list[ft.Control] = []
        for tok in data:
            tid = tok.get("id", "")
            name = tok.get("name", "(unnamed)")
            created = (tok.get("created", "") or "")[:10]
            last_used = (tok.get("last_used") or "—")
            if last_used != "—":
                last_used = last_used[:10]
            perms: list[str] = []
            if tok.get("perm_manage_tokens"):
                perms.append("manage-tokens")
            if tok.get("perm_create_domain"):
                perms.append("create-domain")
            if tok.get("perm_delete_domain"):
                perms.append("delete-domain")
            perm_str = ", ".join(perms) if perms else "basic"

            rows.append(ft.Card(
                content=ft.Container(
                    content=ft.Row(
                        [
                            ft.Column(
                                [
                                    ft.Text(name, weight=ft.FontWeight.W_500),
                                    ft.Text(f"ID: {tid[:12]}…  ·  created {created}  ·  last used {last_used}",
                                            size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                                    ft.Text(f"Permissions: {perm_str}", size=11,
                                            color=ft.Colors.ON_SURFACE_VARIANT),
                                ],
                                expand=True,
                                spacing=2,
                            ),
                            ft.IconButton(
                                ft.Icons.DELETE_OUTLINE,
                                tooltip="Delete token",
                                icon_color=ft.Colors.RED_400,
                                on_click=lambda _, t=tid, n=name: self._on_delete(t, n),
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    padding=12,
                ),
            ))

        self._body.controls = rows
        self._body.update()

    def _show_error(self, msg: str) -> None:
        self._body.controls = [_error_row(msg)]
        self._body.update()

    def _on_new_token(self, _: Any) -> None:
        name_field = ft.TextField(label="Token name", autofocus=True, expand=True)
        perm_manage = ft.Checkbox(label="manage-tokens", value=False)
        perm_create = ft.Checkbox(label="create-domain", value=False)
        perm_delete = ft.Checkbox(label="delete-domain", value=False)
        status = ft.Text("", color=ft.Colors.RED_400)
        busy = ft.ProgressBar(visible=False)

        def _do_create(_: Any) -> None:
            name = name_field.value.strip()
            if not name:
                status.value = "Name is required."
                status.update()
                return
            cmd = ["token", "create", name]
            if perm_manage.value:
                cmd.append("--perm-manage-tokens")
            if perm_create.value:
                cmd.append("--perm-create-domain")
            if perm_delete.value:
                cmd.append("--perm-delete-domain")
            busy.visible = True
            status.value = ""
            busy.update()
            status.update()

            def _bg() -> None:
                try:
                    result = self.app.run(*cmd)
                    secret = result.get("token", "") if isinstance(result, dict) else ""
                    def _done() -> None:
                        self.app.page.close(dlg)
                        self.app.refresh_view(0)
                        if secret:
                            self.app.page.show_dialog(ft.AlertDialog(
                                title=ft.Text("Token created — save your secret"),
                                content=ft.Column([
                                    ft.Text("This secret is shown only once:", size=13),
                                    ft.TextField(value=secret, read_only=True,
                                                 password=False, expand=True),
                                ], tight=True),
                                actions=[ft.TextButton("Close",
                                    on_click=lambda _: self.app.page.close(
                                        self.app.page.dialog))],
                            ))
                    self.app.page.run_thread(_done)
                except Exception as exc:
                    def _err(m: str = str(exc)) -> None:
                        busy.visible = False
                        status.value = m
                        busy.update()
                        status.update()
                    self.app.page.run_thread(_err)

            threading.Thread(target=_bg, daemon=True).start()

        dlg = ft.AlertDialog(
            title=ft.Text("Create API token"),
            content=ft.Container(
                content=ft.Column([
                    name_field,
                    ft.Text("Optional permissions:", size=12),
                    perm_manage, perm_create, perm_delete,
                    busy, status,
                ], tight=True, width=340),
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: self.app.page.close(dlg)),
                ft.ElevatedButton("Create", on_click=_do_create),
            ],
        )
        self.app.page.show_dialog(dlg)

    def _on_delete(self, token_id: str, name: str) -> None:
        def _do(_: Any) -> None:
            self.app.page.close(dlg)
            def _bg() -> None:
                try:
                    self.app.run("token", "delete", token_id)
                    self.app.page.run_thread(lambda: self.app.refresh_view(0))
                except Exception as exc:
                    self.app.page.run_thread(
                        lambda m=str(exc): self.app.page.show_dialog(ft.AlertDialog(
                            title=ft.Text("Error"), content=ft.Text(m),
                            actions=[ft.TextButton("OK",
                                on_click=lambda _: self.app.page.close(
                                    self.app.page.dialog))],
                        ))
                    )
            threading.Thread(target=_bg, daemon=True).start()

        dlg = ft.AlertDialog(
            title=ft.Text("Delete token?"),
            content=ft.Text(f'Delete token "{name}"? This cannot be undone.'),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: self.app.page.close(dlg)),
                ft.ElevatedButton("Delete", on_click=_do,
                                  style=ft.ButtonStyle(color=ft.Colors.RED_400)),
            ],
        )
        self.app.page.show_dialog(dlg)


# ── Domain list view ──────────────────────────────────────────────────────────

class DomainListView:
    def __init__(self, app: DeSECApp) -> None:
        self.app = app
        self._rrset_container = ft.Container(expand=True)
        self._showing_rrsets = False

    def build(self) -> ft.Control:
        self._domain_col = ft.Column([_loading_spinner()], scroll=ft.ScrollMode.AUTO, width=280)
        self._right = ft.Container(
            content=ft.Text("Select a domain to view its records.",
                            italic=True, color=ft.Colors.ON_SURFACE_VARIANT),
            expand=True,
            padding=16,
            alignment=ft.alignment.center,
        )
        self.app.run_bg(
            ("domain", "list"),
            on_result=self._show_domains,
            on_error=self._show_error,
        )
        return ft.Row(
            [
                ft.Container(
                    content=ft.Column(
                        [
                            ft.Container(
                                content=ft.Row([
                                    ft.Text("Domains", weight=ft.FontWeight.W_600, expand=True),
                                    ft.IconButton(ft.Icons.ADD, tooltip="New domain",
                                                  on_click=self._on_new_domain),
                                ]),
                                padding=ft.padding.symmetric(horizontal=8, vertical=8),
                            ),
                            ft.Divider(height=1),
                            ft.Container(content=self._domain_col, expand=True, padding=8),
                        ],
                        spacing=0,
                        expand=True,
                    ),
                    width=280,
                    border=ft.border.only(right=ft.BorderSide(1, ft.Colors.OUTLINE_VARIANT)),
                ),
                self._right,
            ],
            expand=True,
            spacing=0,
        )

    def _show_domains(self, data: Any) -> None:
        if not isinstance(data, list):
            self._domain_col.controls = [_error_row("Unexpected response")]
            self._domain_col.update()
            return
        items: list[ft.Control] = []
        for d in sorted(data, key=lambda x: x.get("name", "")):
            name = d.get("name", "")
            items.append(ft.ListTile(
                title=ft.Text(name),
                subtitle=ft.Text(f"created {(d.get('created','') or '')[:10]}",
                                 size=11, color=ft.Colors.ON_SURFACE_VARIANT),
                trailing=ft.IconButton(
                    ft.Icons.DELETE_OUTLINE, icon_color=ft.Colors.RED_400,
                    tooltip="Delete domain",
                    on_click=lambda _, n=name: self._on_delete_domain(n),
                ),
                on_click=lambda _, n=name: self._open_rrsets(n),
            ))
        if not items:
            items = [ft.Text("No domains.", italic=True,
                             color=ft.Colors.ON_SURFACE_VARIANT)]
        self._domain_col.controls = items
        self._domain_col.update()

    def _show_error(self, msg: str) -> None:
        self._domain_col.controls = [_error_row(msg)]
        self._domain_col.update()

    def _open_rrsets(self, domain: str) -> None:
        view = RRSetView(self.app, domain)
        self._right.content = view.build()
        self._right.update()

    def _on_new_domain(self, _: Any) -> None:
        name_field = ft.TextField(label="Domain name (e.g. example.com)",
                                   autofocus=True, expand=True)
        status = ft.Text("", color=ft.Colors.RED_400)
        busy = ft.ProgressBar(visible=False)

        def _do(_: Any) -> None:
            name = name_field.value.strip()
            if not name:
                status.value = "Domain name required."
                status.update()
                return
            busy.visible = True
            status.value = ""
            busy.update()
            status.update()

            def _bg() -> None:
                try:
                    self.app.run("domain", "create", name)
                    def _done() -> None:
                        self.app.page.close(dlg)
                        self.app.refresh_view(1)
                    self.app.page.run_thread(_done)
                except Exception as exc:
                    def _err(m: str = str(exc)) -> None:
                        busy.visible = False
                        status.value = m
                        busy.update()
                        status.update()
                    self.app.page.run_thread(_err)

            threading.Thread(target=_bg, daemon=True).start()

        dlg = ft.AlertDialog(
            title=ft.Text("Create domain"),
            content=ft.Container(
                content=ft.Column([name_field, busy, status], tight=True, width=340),
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: self.app.page.close(dlg)),
                ft.ElevatedButton("Create", on_click=_do),
            ],
        )
        self.app.page.show_dialog(dlg)

    def _on_delete_domain(self, name: str) -> None:
        def _do(_: Any) -> None:
            self.app.page.close(dlg)
            def _bg() -> None:
                try:
                    self.app.run("domain", "delete", name, "--yes")
                    self.app.page.run_thread(lambda: self.app.refresh_view(1))
                except Exception as exc:
                    self.app.page.run_thread(
                        lambda m=str(exc): self.app.page.show_dialog(ft.AlertDialog(
                            title=ft.Text("Error"), content=ft.Text(m),
                            actions=[ft.TextButton("OK",
                                on_click=lambda _: self.app.page.close(
                                    self.app.page.dialog))],
                        ))
                    )
            threading.Thread(target=_bg, daemon=True).start()

        dlg = ft.AlertDialog(
            title=ft.Text("Delete domain?"),
            content=ft.Text(f'Delete "{name}" and ALL its records? This cannot be undone.'),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: self.app.page.close(dlg)),
                ft.ElevatedButton("Delete", on_click=_do,
                                  style=ft.ButtonStyle(color=ft.Colors.RED_400)),
            ],
        )
        self.app.page.show_dialog(dlg)


# ── RRSet view (records for one domain) ──────────────────────────────────────

class RRSetView:
    def __init__(self, app: DeSECApp, domain: str) -> None:
        self.app = app
        self.domain = domain

    def build(self) -> ft.Control:
        self._body = ft.Column([_loading_spinner()], expand=True, scroll=ft.ScrollMode.AUTO)
        self._load()
        return ft.Column(
            [
                ft.Container(
                    content=ft.Row([
                        ft.Text(self.domain, size=16, weight=ft.FontWeight.W_600, expand=True),
                        ft.ElevatedButton("Add record", icon=ft.Icons.ADD,
                                          on_click=self._on_add_record),
                    ]),
                    padding=ft.padding.symmetric(horizontal=16, vertical=12),
                ),
                ft.Divider(height=1),
                ft.Container(content=self._body, expand=True, padding=16),
            ],
            expand=True,
            spacing=0,
        )

    def _load(self) -> None:
        self._body.controls = [_loading_spinner()]
        self._body.update() if hasattr(self._body, "page") and self._body.page else None
        self.app.run_bg(
            ("record", "list", self.domain),
            on_result=self._show_records,
            on_error=self._show_error,
        )

    def _show_records(self, data: Any) -> None:
        if not isinstance(data, list):
            self._body.controls = [_error_row("Unexpected response")]
            self._body.update()
            return
        if not data:
            self._body.controls = [ft.Text("No records.", italic=True,
                                           color=ft.Colors.ON_SURFACE_VARIANT)]
            self._body.update()
            return

        # Group by type
        by_type: dict[str, list[dict]] = {}
        for rec in sorted(data, key=lambda r: (r.get("type",""), r.get("subname",""))):
            t = rec.get("type", "?")
            by_type.setdefault(t, []).append(rec)

        rows: list[ft.Control] = []
        for rtype, records in sorted(by_type.items()):
            rows.append(ft.Text(rtype, weight=ft.FontWeight.W_600, size=13,
                                color=ft.Colors.TEAL_400))
            for rec in records:
                subname = rec.get("subname", "") or "@"
                ttl = rec.get("ttl", "")
                rdata = rec.get("records", [])
                rdata_str = " | ".join(rdata[:3])
                if len(rdata) > 3:
                    rdata_str += f" (+{len(rdata)-3} more)"
                rows.append(ft.Card(
                    content=ft.Container(
                        content=ft.Row([
                            ft.Column([
                                ft.Text(f"{subname}.{self.domain}" if subname != "@"
                                        else self.domain, size=13),
                                ft.Text(f"TTL {ttl}  ·  {rdata_str}", size=11,
                                        color=ft.Colors.ON_SURFACE_VARIANT),
                            ], expand=True, spacing=1),
                            ft.IconButton(
                                ft.Icons.EDIT_OUTLINED, tooltip="Edit",
                                on_click=lambda _, r=rec: self._on_edit_record(r),
                            ),
                            ft.IconButton(
                                ft.Icons.DELETE_OUTLINE, tooltip="Delete",
                                icon_color=ft.Colors.RED_400,
                                on_click=lambda _, r=rec: self._on_delete_record(r),
                            ),
                        ]),
                        padding=ft.padding.symmetric(horizontal=12, vertical=8),
                    ),
                ))
        self._body.controls = rows
        self._body.update()

    def _show_error(self, msg: str) -> None:
        self._body.controls = [_error_row(msg)]
        self._body.update()

    def _record_dialog(self, title: str, on_save: "callable", rec: dict | None = None) -> None:
        subname_f = ft.TextField(label="Subname (leave blank for @)", expand=True,
                                  value=rec.get("subname", "") if rec else "")
        type_f = ft.Dropdown(
            label="Record type",
            width=160,
            value=rec.get("type", "A") if rec else "A",
            options=[ft.dropdown.Option(t) for t in
                     ["A", "AAAA", "CNAME", "MX", "TXT", "NS", "CAA", "SRV",
                      "TLSA", "PTR", "SOA", "SPF", "SSHFP"]],
        )
        ttl_f = ft.TextField(label="TTL (seconds)", width=120,
                              value=str(rec.get("ttl", 3600)) if rec else "3600",
                              keyboard_type=ft.KeyboardType.NUMBER)
        existing = "\n".join(rec.get("records", [])) if rec else ""
        rdata_f = ft.TextField(label="Records (one per line)", multiline=True, min_lines=3,
                                max_lines=8, expand=True, value=existing)
        status = ft.Text("", color=ft.Colors.RED_400)
        busy = ft.ProgressBar(visible=False)

        def _do(_: Any) -> None:
            rdata_lines = [l.strip() for l in (rdata_f.value or "").splitlines() if l.strip()]
            if not rdata_lines:
                status.value = "At least one record value required."
                status.update()
                return
            busy.visible = True
            status.value = ""
            busy.update()
            status.update()
            args = [
                "--subname", subname_f.value.strip(),
                "--type", type_f.value or "A",
                "--ttl", ttl_f.value.strip() or "3600",
            ]
            for rd in rdata_lines:
                args += ["--rdata", rd]
            on_save(args, dlg, busy, status)

        dlg = ft.AlertDialog(
            title=ft.Text(title),
            content=ft.Container(
                content=ft.Column([
                    ft.Row([subname_f, type_f, ttl_f]),
                    rdata_f,
                    busy, status,
                ], tight=True, width=480),
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: self.app.page.close(dlg)),
                ft.ElevatedButton("Save", on_click=_do),
            ],
        )
        self.app.page.show_dialog(dlg)

    def _on_add_record(self, _: Any) -> None:
        def _save(args: list[str], dlg: Any, busy: Any, status: Any) -> None:
            def _bg() -> None:
                try:
                    self.app.run("record", "add", self.domain, *args)
                    def _done() -> None:
                        self.app.page.close(dlg)
                        self._load()
                    self.app.page.run_thread(_done)
                except Exception as exc:
                    def _err(m: str = str(exc)) -> None:
                        busy.visible = False
                        status.value = m
                        busy.update()
                        status.update()
                    self.app.page.run_thread(_err)
            threading.Thread(target=_bg, daemon=True).start()
        self._record_dialog("Add DNS record", _save)

    def _on_edit_record(self, rec: dict) -> None:
        def _save(args: list[str], dlg: Any, busy: Any, status: Any) -> None:
            def _bg() -> None:
                try:
                    self.app.run("record", "edit", self.domain, *args)
                    def _done() -> None:
                        self.app.page.close(dlg)
                        self._load()
                    self.app.page.run_thread(_done)
                except Exception as exc:
                    def _err(m: str = str(exc)) -> None:
                        busy.visible = False
                        status.value = m
                        busy.update()
                        status.update()
                    self.app.page.run_thread(_err)
            threading.Thread(target=_bg, daemon=True).start()
        self._record_dialog("Edit DNS record", _save, rec)

    def _on_delete_record(self, rec: dict) -> None:
        subname = rec.get("subname", "")
        rtype = rec.get("type", "")

        def _do(_: Any) -> None:
            self.app.page.close(dlg)
            def _bg() -> None:
                args = ["record", "delete", self.domain, "--type", rtype, "--yes"]
                if subname:
                    args += ["--subname", subname]
                try:
                    self.app.run(*args)
                    self.app.page.run_thread(self._load)
                except Exception as exc:
                    self.app.page.run_thread(
                        lambda m=str(exc): self.app.page.show_dialog(ft.AlertDialog(
                            title=ft.Text("Error"), content=ft.Text(m),
                            actions=[ft.TextButton("OK",
                                on_click=lambda _: self.app.page.close(
                                    self.app.page.dialog))],
                        ))
                    )
            threading.Thread(target=_bg, daemon=True).start()

        label = f"{subname or '@'} {rtype}"
        dlg = ft.AlertDialog(
            title=ft.Text("Delete record?"),
            content=ft.Text(f'Delete "{label}" record set? This cannot be undone.'),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: self.app.page.close(dlg)),
                ft.ElevatedButton("Delete", on_click=_do,
                                  style=ft.ButtonStyle(color=ft.Colors.RED_400)),
            ],
        )
        self.app.page.show_dialog(dlg)


# ── Provisioning view ─────────────────────────────────────────────────────────

class ProvisioningView:
    def __init__(self, app: DeSECApp) -> None:
        self.app = app

    def build(self) -> ft.Control:
        return ft.Column(
            [
                ft.Container(
                    ft.Text("Provisioning", size=18, weight=ft.FontWeight.W_600),
                    padding=ft.padding.symmetric(horizontal=16, vertical=12),
                ),
                ft.Divider(height=1),
                ft.Container(
                    content=ft.Tabs(
                        tabs=[
                            ft.Tab(text="DDNS token", content=self._ddns_tab()),
                            ft.Tab(text="Cert token", content=self._cert_tab()),
                            ft.Tab(text="Multi-cert", content=self._multicert_tab()),
                        ],
                        expand=True,
                    ),
                    expand=True,
                    padding=16,
                ),
            ],
            expand=True,
            spacing=0,
        )

    def _result_box(self) -> tuple[ft.TextField, ft.Text, ft.ProgressBar]:
        out = ft.TextField(read_only=True, multiline=True, min_lines=4, max_lines=10,
                           expand=True, text_style=ft.TextStyle(font_family="monospace", size=11))
        status = ft.Text("", color=ft.Colors.RED_400)
        busy = ft.ProgressBar(visible=False)
        return out, status, busy

    def _run_and_show(self, args: list[str], out: ft.TextField,
                      status: ft.Text, busy: ft.ProgressBar) -> None:
        busy.visible = True
        status.value = ""
        out.value = ""
        busy.update()
        status.update()
        out.update()

        def _bg() -> None:
            try:
                result = self.app.run(*args)
                text = json.dumps(result, indent=2) if isinstance(result, (dict, list)) else str(result)
                def _done() -> None:
                    busy.visible = False
                    out.value = text
                    busy.update()
                    out.update()
                self.app.page.run_thread(_done)
            except Exception as exc:
                def _err(m: str = str(exc)) -> None:
                    busy.visible = False
                    status.value = m
                    busy.update()
                    status.update()
                self.app.page.run_thread(_err)

        threading.Thread(target=_bg, daemon=True).start()

    def _ddns_tab(self) -> ft.Control:
        domain_f = ft.TextField(label="Domain", expand=True)
        subname_f = ft.TextField(label="Subname (optional)", expand=True)
        ipv4_f = ft.TextField(label="IPv4 (optional)", expand=True)
        ipv6_f = ft.TextField(label="IPv6 (optional)", expand=True)
        ttl_f = ft.TextField(label="TTL", value="3600", width=100)
        tname_f = ft.TextField(label="Token name (optional)", expand=True)
        out, status, busy = self._result_box()

        def _run(_: Any) -> None:
            args = ["ddns-add", domain_f.value.strip()]
            if subname_f.value.strip():
                args += ["--subname", subname_f.value.strip()]
            if ipv4_f.value.strip():
                args += ["--ipv4", ipv4_f.value.strip()]
            if ipv6_f.value.strip():
                args += ["--ipv6", ipv6_f.value.strip()]
            if ttl_f.value.strip():
                args += ["--ttl", ttl_f.value.strip()]
            if tname_f.value.strip():
                args += ["--token-name", tname_f.value.strip()]
            self._run_and_show(args, out, status, busy)

        return ft.Container(
            content=ft.Column([
                ft.Text("Provision a DDNS update token scoped to a single domain/subname.",
                        size=13, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Row([domain_f, subname_f]),
                ft.Row([ipv4_f, ipv6_f, ttl_f]),
                tname_f,
                ft.ElevatedButton("Create DDNS token", icon=ft.Icons.ADD, on_click=_run),
                busy, status, out,
            ], spacing=10, scroll=ft.ScrollMode.AUTO),
            padding=ft.padding.only(top=12),
        )

    def _cert_tab(self) -> ft.Control:
        domain_f = ft.TextField(label="Domain", expand=True)
        subname_f = ft.TextField(label="Subname (optional)", expand=True)
        cname_f = ft.TextField(label="CNAME target (optional)", expand=True)
        ttl_f = ft.TextField(label="TTL", value="3600", width=100)
        tname_f = ft.TextField(label="Token name (optional)", expand=True)
        out, status, busy = self._result_box()

        def _run(_: Any) -> None:
            args = ["cert-add", domain_f.value.strip()]
            if subname_f.value.strip():
                args += ["--subname", subname_f.value.strip()]
            if cname_f.value.strip():
                args += ["--cname", cname_f.value.strip()]
            if ttl_f.value.strip():
                args += ["--ttl", ttl_f.value.strip()]
            if tname_f.value.strip():
                args += ["--token-name", tname_f.value.strip()]
            self._run_and_show(args, out, status, busy)

        return ft.Container(
            content=ft.Column([
                ft.Text("Provision a certificate token with TXT-record write access.",
                        size=13, color=ft.Colors.ON_SURFACE_VARIANT),
                ft.Row([domain_f, subname_f]),
                ft.Row([cname_f, ttl_f]),
                tname_f,
                ft.ElevatedButton("Create cert token", icon=ft.Icons.ADD, on_click=_run),
                busy, status, out,
            ], spacing=10, scroll=ft.ScrollMode.AUTO),
            padding=ft.padding.only(top=12),
        )

    def _multicert_tab(self) -> ft.Control:
        entries_f = ft.TextField(
            label='Domains (one per line, format: "domain" or "domain:subname")',
            multiline=True, min_lines=4, max_lines=8, expand=True,
        )
        tname_f = ft.TextField(label="Token name", expand=True)
        out, status, busy = self._result_box()

        def _run(_: Any) -> None:
            entries = [l.strip() for l in (entries_f.value or "").splitlines() if l.strip()]
            if not entries:
                status.value = "At least one domain required."
                status.update()
                return
            if not tname_f.value.strip():
                status.value = "Token name required."
                status.update()
                return
            args = ["cert-multi", "--token-name", tname_f.value.strip()]
            for e in entries:
                args += ["--entry", e]
            self._run_and_show(args, out, status, busy)

        return ft.Container(
            content=ft.Column([
                ft.Text("Provision a single token covering TXT records for multiple domains.",
                        size=13, color=ft.Colors.ON_SURFACE_VARIANT),
                entries_f,
                tname_f,
                ft.ElevatedButton("Create multi-cert token", icon=ft.Icons.ADD, on_click=_run),
                busy, status, out,
            ], spacing=10, scroll=ft.ScrollMode.AUTO),
            padding=ft.padding.only(top=12),
        )


# ── Entry point ───────────────────────────────────────────────────────────────

def main(page: ft.Page) -> None:
    DeSECApp(page)


if __name__ == "__main__":
    if not SCRIPT.exists():
        print(f"ERROR: desec-api.py not found at {SCRIPT}", file=sys.stderr)
        print("Make sure you're running from the mech-goodies repo root.", file=sys.stderr)
        sys.exit(1)
    ft.app(target=main)
