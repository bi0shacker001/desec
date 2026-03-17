# tui/screens/__init__.py
#
# This package contains all full-screen views for the desec TUI.
#
#   login.py     — LoginScreen: first screen shown; asks for the master API token
#   tokens.py    — TokenListScreen: main screen listing all tokens; also CreateTokenModal
#   policies.py  — PolicyScreen: view/manage policies for a selected token; AddPolicyModal
#   domains.py   — DomainListScreen: list/create/delete domains; CreateDomainModal
#   rrsets.py    — RRSetScreen: list/add/edit/delete DNS records; AddEditRRSetModal
#   provision.py — Provisioning wizards: DdnsAddModal, CertAddModal, CertMultiScreen
#
# SCREEN NAVIGATION:
#   app.push_screen(SomeScreen()) — add SomeScreen on top of the stack
#   app.pop_screen()              — go back to the previous screen
#   app.push_screen(SomeModal(), callback) — open a modal and call callback(result) on close
