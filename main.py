# main.py
# 앱 시작 — 크래시 로거 설치 → QApplication → MainWindow

import sys
import os
import logging
import traceback
from pathlib import Path

# shared submodule을 import 경로에 추가
sys.path.insert(0, str(Path(__file__).parent))

from config.settings import LOG_DIR


def _setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # 파일: DEBUG 전부 기록 / 콘솔: INFO 이상만 출력
    file_handler = logging.FileHandler(LOG_DIR / "app.log", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[console_handler, file_handler],
    )

    # 내부 라이브러리 노이즈 억제 (콘솔 + 파일 모두)
    for _noisy in (
        "ibapi.client", "ibapi.decoder", "ibapi.wrapper",
        "ibapi.connection", "ibapi.reader", "ibapi.comm",
        "ibapi.utils", "ibapi.order", "ibapi.contract",
        "shared.broker.mock_broker",  # 주문 체결 debug 로그
        "shared.core.bus",            # pub/sub debug 로그
    ):
        logging.getLogger(_noisy).setLevel(logging.WARNING)


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
