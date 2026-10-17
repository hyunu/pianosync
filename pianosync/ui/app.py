"""Application entry point."""

from __future__ import annotations

import os
import sys


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from .main_window import MainWindow
    from .theme import apply_theme

    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv)
    app.setApplicationName("Piano Sync Switcher")
    app.setOrganizationName("pianosync")
    app.setApplicationDisplayName("Piano Sync Switcher")
    apply_theme(app)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())