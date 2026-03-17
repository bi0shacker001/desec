# gui/__init__.py
#
# This package contains the PySide6/Qt native GUI for the desec program.
# PySide6 is the official Python binding for Qt6.
#
# WHY PySide6?
#   - Native-looking on KDE/Linux (uses the same toolkit as Plasma)
#   - Better suited for desktop use than web-based GUIs
#   - Runs without a web browser
#
# CURRENT STATUS:
#   The GUI is a stub — the full implementation is planned for a future version.
#   The TUI (tui/) is the primary interactive interface for now.
#
# HOW TO INSTALL:
#   pip install PySide6
#
# ENTRY POINT:
#   desec.py passes --gui to launch gui.app.main() instead of the TUI.
