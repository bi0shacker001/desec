# tui/__init__.py
#
# This package contains the Textual terminal user interface for the desec program.
#
# Structure:
#   app.py         — DeSECApp class (the root Textual application) and CSS constants
#   widgets.py     — Reusable modal dialogs (MessageModal, ConfirmModal, NewSecretModal)
#   screens/       — Individual full-screen views
#     login.py     — LoginScreen: token entry and validation
#     tokens.py    — CreateTokenModal + TokenListScreen: main token management screen
#     policies.py  — AddPolicyModal + PolicyScreen: per-token policy editor
#     domains.py   — CreateDomainModal + DomainListScreen: domain registration
#     rrsets.py    — AddEditRRSetModal + RRSetScreen: DNS record editor
#     provision.py — DdnsAddModal + CertAddModal + CertMultiScreen: provisioning wizards
#
# TEXTUAL VERSION NOTE:
#   This code targets Textual 8.x.  Several Textual 8 gotchas are documented
#   in the CSS (tui/app.py) and inline.  Key issues to know:
#     - Input widgets are visually invisible in the default dark theme unless
#       the App-level CSS overrides the border color.
#     - ScrollableContainer must use height: auto (not height: 1fr) or children
#       collapse to zero height.
#   See the CSS comment block in tui/app.py for full details.
