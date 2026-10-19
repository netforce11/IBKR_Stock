# core/storage/scanner_storage.py
# 스캐너 조회 결과 일자/시간별 JSON 저장 & 불러오기
#
# 저장 경로 구조:
#   <data_dir>/scanner/
#     YYYYMMDD/
#       HHMM_<SCAN_CODE>.json
#       HHMM_<SCAN_CODE>.json
#       ...
#
# JSON 포맷:
#   {
#     "scan_code": "TOP_PERC_GAIN",
#     "saved_at":  "2026-10-20 09:35",
#     "ts":        1761000000.0,
#     "results": [
#       {"rank":1, "symbol":"NVDA", "price":900.5, "change_pct":4.2, ...},
#       ...
#     ]
#   }

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Iterator

log = logging.getLogger(__name__)


class ScannerStorage:
    """
    스캐너 결과 파일 I/O.

    사용법:
        store = ScannerStorage(DATA_DIR)
        store.save(scan_result_dict)          # 자동 경로 생성
        dates = store.list_dates()            # ["20261020", ...]
        files = store.list_files("20261020")  # [(hhmm, scan_code, path), ...]
        data  = store.load(path)              # dict
    """

    def __init__(self, data_dir: Path | str) -> None:
        self._root = Path(data_dir) / "scanner"
        self._root.mkdir(parents=True, exist_ok=True)

    # ── 저장 ────────────────────────────────────────────────
    def save(self, result: dict) -> Path:
        """
        result = {"scan_code": str, "ts": float, "results": [...]}
        반환: 저장된 파일 Path
        """
        ts        = result.get("ts", time.time())
        dt        = datetime.fromtimestamp(ts)
        scan_code = result.get("scan_code", "UNKNOWN").replace("/", "_")

        date_str = dt.strftime("%Y%m%d")
        time_str = dt.strftime("%H%M")

        day_dir = self._root / date_str
        day_dir.mkdir(parents=True, exist_ok=True)

        filename = f"{time_str}_{scan_code}.json"
        # 같은 분에 여러 번 저장되면 _2, _3 접미사
        path = day_dir / filename
        suffix = 2
        while path.exists():
            path = day_dir / f"{time_str}_{scan_code}_{suffix}.json"
            suffix += 1

        payload = {
            "scan_code": result.get("scan_code", ""),
            "saved_at":  dt.strftime("%Y-%m-%d %H:%M"),
            "ts":        ts,
            "results":   result.get("results", []),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
        log.info("ScannerStorage saved: %s (%d rows)",
                 path, len(payload["results"]))
        return path

    # ── 날짜 목록 ────────────────────────────────────────────
    def list_dates(self) -> list[str]:
        """저장된 날짜 폴더 목록 (YYYYMMDD), 최신순"""
        dirs = sorted(
            (d.name for d in self._root.iterdir() if d.is_dir()),
            reverse=True
        )
        return dirs

    # ── 특정 날짜의 파일 목록 ──────────────────────────────
    def list_files(self, date_str: str) -> list[tuple[str, str, Path]]:
        """
        반환: [(hhmm, scan_code, path), ...] 최신순
        예:   [("1430", "TOP_PERC_GAIN", Path(...))]
        """
        day_dir = self._root / date_str
        if not day_dir.exists():
            return []
        results = []
        for f in sorted(day_dir.glob("*.json"), reverse=True):
            stem  = f.stem          # "1430_TOP_PERC_GAIN" 또는 "1430_TOP_PERC_GAIN_2"
            parts = stem.split("_", 1)
            if len(parts) < 2:
                continue
            hhmm, code_rest = parts[0], parts[1]
            # _2, _3 접미사 제거
            scan_code = code_rest.rsplit("_", 1)
            if len(scan_code) == 2 and scan_code[1].isdigit():
                scan_code = scan_code[0]
            else:
                scan_code = code_rest
            results.append((hhmm, scan_code, f))
        return results

    # ── 파일 불러오기 ────────────────────────────────────────
    def load(self, path: Path | str) -> dict:
        """저장된 JSON 파일 읽기. 오류 시 빈 dict 반환"""
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            log.error("ScannerStorage.load error [%s]: %s", path, e)
            return {}

    # ── 전체 파일 순회 ───────────────────────────────────────
    def iter_all(self) -> Iterator[tuple[str, str, str, Path]]:
        """
        전체 파일 순회 — (date_str, hhmm, scan_code, path)
        날짜 내림차순, 시간 내림차순
        """
        for date_str in self.list_dates():
            for hhmm, scan_code, path in self.list_files(date_str):
                yield date_str, hhmm, scan_code, path

    # ── 특정 날짜+코드 최신 파일 ─────────────────────────────
    def latest(self, date_str: str, scan_code: str) -> dict | None:
        """특정 날짜의 특정 스캔코드 가장 최근 파일 로드"""
        for hhmm, code, path in self.list_files(date_str):
            if code == scan_code:
                return self.load(path)
        return None
