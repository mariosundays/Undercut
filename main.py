"""Undercut - in/out video trimmer with a live export-size readout."""

import sys

from PySide6.QtWidgets import QApplication

from app import encoder, proxy
from app.window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Undercut")
    encoder.sweep_temp()
    proxy.sweep()

    window = MainWindow()
    # A file passed on the command line opens straight away.
    if len(sys.argv) > 1:
        window.load(sys.argv[1])
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
