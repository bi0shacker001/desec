# gui/app.py
#
# PySide6/Qt native GUI stub for the desec program.
#
# This is a placeholder for the full Qt GUI.  Currently it just shows a
# message window explaining that the GUI is not yet implemented and directing
# the user to use the TUI instead.
#
# WHEN IMPLEMENTING THE FULL GUI:
#   - Replace the stub window below with real Qt widgets
#   - Use QTreeView / QTableView for token/domain/record lists
#   - Use QDialog subclasses for forms (create token, add policy, etc.)
#   - All API calls should be made in QThread workers to keep the UI responsive
#   - Import from core.api and core.env — the same functions the TUI uses
#
# PYSIDE6 BASICS FOR FUTURE MAINTAINERS:
#   - QApplication is the root object; every Qt app needs exactly one
#   - QMainWindow is the main window with a menu bar, status bar, etc.
#   - Widgets are added to a layout (QVBoxLayout, QHBoxLayout, QGridLayout)
#   - exec() starts the Qt event loop and blocks until the window is closed
#   - sys.exit(app.exec()) is the standard pattern: propagates the exit code

from __future__ import annotations

import sys


def main() -> None:
    """
    Launch the PySide6 Qt GUI.

    Currently shows a "not yet implemented" placeholder window.
    Replace this function body with real Qt code when the GUI is built.

    This function is called by desec.py when the user passes --gui.
    """
    try:
        # Try to import PySide6 — it's an optional dependency
        from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget
        from PySide6.QtCore import Qt
    except ImportError:
        # PySide6 is not installed — print a helpful error and exit
        print(
            "PySide6 is required for the GUI.  Install it with:\n"
            "  pip install PySide6\n\n"
            "Or use the TUI instead (no extra dependencies beyond textual):\n"
            "  desec --tui",
            file=sys.stderr,
        )
        sys.exit(1)

    # ── Placeholder window ──────────────────────────────────────────────────
    # Remove everything below this line when implementing the real GUI.

    app = QApplication(sys.argv)

    # Main window
    window = QMainWindow()
    window.setWindowTitle("deSEC Manager")
    window.resize(640, 400)

    # Central widget with a simple layout
    central = QWidget()
    layout = QVBoxLayout(central)

    # Placeholder message
    label = QLabel(
        "<h2>deSEC Manager GUI</h2>"
        "<p>The Qt GUI is not yet implemented.</p>"
        "<p>Please use the TUI (terminal interface) for now:</p>"
        "<pre>  desec</pre>"
        "<p>Or use the CLI for scripting:</p>"
        "<pre>  desec token list\n  desec domain list</pre>"
    )
    label.setAlignment(Qt.AlignmentFlag.AlignCenter)
    label.setTextFormat(Qt.TextFormat.RichText)
    layout.addWidget(label)

    window.setCentralWidget(central)
    window.show()

    # exec() starts the Qt event loop; doesn't return until window is closed
    sys.exit(app.exec())
