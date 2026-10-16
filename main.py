# main.py
# 앱 시작 — 크래시 로거 설치 → QApplication → MainWindow

import sys
import logging
import traceback
from pathlib import Path
from config.settings import LOG_DIR


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8"),
        ],
    )


def _install_crash_logger() -> None:
    """QApplication 생성 전 크래시 훅 설치"""
    def handler(exc_type, exc_value, exc_tb):
        logging.getLogger("CRASH").critical(
            "Uncaught exception:\n%s",
            "".join(traceback.format_exception(exc_type, exc_value, exc_tb)),
        )
        sys.__excepthook__(exc_type, exc_value, exc_tb)
    sys.excepthook = handler


def main() -> None:
    _setup_logging()
    _install_crash_logger()

    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setApplicationName("IBKR Stock HTS")

    # Qt 예외 훅 설치
    import PyQt6.QtCore as QtCore
    def qt_message_handler(mode, context, message):
        logging.getLogger("Qt").warning("[%s] %s", mode, message)
    QtCore.qInstallMessageHandler(qt_message_handler)

    from ui.main_window import MainWindow
    win = MainWindow()
    win.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
