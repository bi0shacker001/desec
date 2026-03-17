# core/__init__.py
#
# This package contains the non-UI heart of the desec program:
#
#   env.py    — reads and writes the ~/.config/mech-goodies/desec.env config file
#   api.py    — all HTTP calls to the deSEC REST API (tokens, domains, records, etc.)
#   output.py — CLI output helpers (table, json, yaml) and token validation
#
# Nothing in this package imports from `tui/` or `gui/`, so it can be used
# safely from scripts, cron jobs, or tests without launching any UI.
