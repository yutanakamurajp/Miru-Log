from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psutil
import pystray
from dotenv import load_dotenv
from PIL import Image, ImageDraw

from mirulog.logging_utils import init_logger

STATE_FILE = "tray_state.json"


@dataclass(frozen=True)
class ProgramSpec:
    script: str
    label: str
    mode: str  # "daemon" or "oneshot"


@dataclass(frozen=True)
class TraySettings:
    repo_root: Path
    log_dir: Path
    output_dir: Path
    report_dir: Path
    data_dir: Path
    log_level: str


def load_settings() -> TraySettings:
    repo_root = Path(__file__).resolve().parent
    dotenv_path = repo_root / ".env"
    load_dotenv(dotenv_path=dotenv_path, override=False, encoding="utf-8-sig")
    log_dir = Path(os.getenv("LOG_DIR", "logs")).resolve()
    output_dir = Path(os.getenv("SUMMARY_OUTPUT_DIR", "output")).resolve()
    report_dir = Path(os.getenv("REPORT_EXPORT_DIR", "reports")).resolve()
    data_dir = Path(os.getenv("DATA_DIR", "data")).resolve()
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    return TraySettings(
        repo_root=repo_root,
        log_dir=log_dir,
        output_dir=output_dir,
        report_dir=report_dir,
        data_dir=data_dir,
        log_level=log_level,
    )


class TrayController:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.logger = init_logger("tray", self.settings.log_dir, self.settings.log_level)
        self.state_path = self.settings.data_dir / STATE_FILE
        self.state: dict[str, Any] = self._load_state()
        self.analyzer_backend = self._load_analyzer_backend()
        self.processes: dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()
        self.programs = [
            ProgramSpec("observer.py", "Observer", "daemon"),
            ProgramSpec("analyzer.py", "Analyzer", "oneshot"),
            ProgramSpec("summarizer.py", "Summarizer", "oneshot"),
            ProgramSpec("notifier.py", "Notifier", "oneshot"),
        ]
        self.icon = pystray.Icon(
            "Miru-Log",
            _create_icon(),
            "Miru-Log",
            self._build_menu(),
        )

    def run(self) -> None:
        self.icon.run()

    def _build_menu(self) -> pystray.Menu:
        items = []
        for program in self.programs:
            items.append(self._program_menu(program))
        items.extend(
            [
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("解析バックエンド", self._backend_menu()),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("ログフォルダを開く", self._open_logs),
                pystray.MenuItem("出力フォルダを開く", self._open_output),
                pystray.MenuItem("レポートフォルダを開く", self._open_reports),
                pystray.MenuItem("データフォルダを開く", self._open_data),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("終了", self._quit),
            ]
        )
        return pystray.Menu(*items)

    def _backend_menu(self) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(
                "Gemini",
                lambda *_: self._set_analyzer_backend("gemini"),
                checked=lambda _: self.analyzer_backend == "gemini",
            ),
            pystray.MenuItem(
                "Local (LM Studio)",
                lambda *_: self._set_analyzer_backend("local"),
                checked=lambda _: self.analyzer_backend == "local",
            ),
        )

    def _program_menu(self, program: ProgramSpec) -> pystray.MenuItem:
        status_item = pystray.MenuItem(
            lambda _: self._status_text(program),
            None,
            enabled=False,
        )
        if program.mode == "daemon":
            actions = [
                pystray.MenuItem(
                    "開始",
                    lambda *_: self._start_daemon(program),
                    enabled=lambda _: not self._is_running(program.script),
                ),
                pystray.MenuItem(
                    "停止",
                    lambda *_: self._stop_program(program),
                    enabled=lambda _: self._is_running(program.script),
                ),
            ]
        else:
            actions = [
                pystray.MenuItem(
                    "実行",
                    lambda *_: self._run_once(program),
                    enabled=lambda _: not self._is_running(program.script),
                ),
                pystray.MenuItem(
                    "停止",
                    lambda *_: self._stop_program(program),
                    enabled=lambda _: self._is_running(program.script),
                ),
            ]
        return pystray.MenuItem(program.label, pystray.Menu(status_item, *actions))

    def _start_daemon(self, program: ProgramSpec) -> None:
        if self._is_running(program.script):
            self.logger.info("%s is already running", program.label)
            return
        self._spawn(program.script)
        now = datetime.now().isoformat()
        self._update_state(program.script, last_start=now, last_end=None)
        self._refresh_menu()

    def _run_once(self, program: ProgramSpec) -> None:
        if self._is_running(program.script):
            self.logger.info("%s is already running", program.label)
            return
        proc = self._spawn(program.script)
        now = datetime.now().isoformat()
        self._update_state(program.script, last_start=now, last_end=None)
        thread = threading.Thread(
            target=self._wait_process,
            args=(program, proc),
            daemon=True,
        )
        thread.start()
        self._refresh_menu()

    def _spawn(self, script: str) -> subprocess.Popen:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = os.environ.copy()
        if script == "analyzer.py":
            env["ANALYZER_BACKEND"] = self.analyzer_backend
        proc = subprocess.Popen(
            [sys.executable, script],
            cwd=self.settings.repo_root,
            creationflags=creation_flags,
            env=env,
        )
        with self.lock:
            self.processes[script] = proc
        return proc

    def _wait_process(self, program: ProgramSpec, proc: subprocess.Popen) -> None:
        exit_code = proc.wait()
        finished = datetime.now().isoformat()
        self._update_state(program.script, last_end=finished, last_exit=exit_code)
        with self.lock:
            if self.processes.get(program.script) is proc:
                self.processes.pop(program.script, None)
        self.logger.info("%s finished with code %s", program.label, exit_code)
        self._refresh_menu()

    def _stop_program(self, program: ProgramSpec) -> None:
        processes = self._find_processes(program.script)
        if not processes:
            self.logger.info("%s is not running", program.label)
            return
        for proc in processes:
            try:
                proc.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        gone, alive = psutil.wait_procs(processes, timeout=5)
        for proc in alive:
            try:
                proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        self.logger.info("%s stopped (%s terminated, %s killed)", program.label, len(gone), len(alive))
        self._update_state(program.script, last_end=datetime.now().isoformat())
        self._refresh_menu()

    def _open_logs(self, *_: Any) -> None:
        self._open_dir(self.settings.log_dir)

    def _open_output(self, *_: Any) -> None:
        self._open_dir(self.settings.output_dir)

    def _open_reports(self, *_: Any) -> None:
        self._open_dir(self.settings.report_dir)

    def _open_data(self, *_: Any) -> None:
        self._open_dir(self.settings.data_dir)

    def _open_dir(self, path: Path) -> None:
        try:
            path.mkdir(parents=True, exist_ok=True)
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:
            self.logger.warning("Failed to open %s: %s", path, exc)

    def _quit(self, icon: Any, _: Any) -> None:
        icon.stop()

    def _refresh_menu(self) -> None:
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def _status_text(self, program: ProgramSpec) -> str:
        running, started_at = self._running_info(program.script)
        if running:
            started_text = _format_time(started_at)
            return f"状態: 実行中 (開始 {started_text})"
        last_end = self._state_time(program.script, "last_end")
        last_start = self._state_time(program.script, "last_start")
        last_text = _format_time(last_end or last_start)
        if last_text == "-":
            return "状態: 停止中 (最終 -)"
        return f"状態: 停止中 (最終 {last_text})"

    def _state_time(self, script: str, key: str) -> datetime | None:
        entry = self.state.get(script, {})
        raw = entry.get(key)
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None

    def _is_running(self, script: str) -> bool:
        return bool(self._find_processes(script))

    def _running_info(self, script: str) -> tuple[bool, datetime | None]:
        processes = self._find_processes(script)
        if not processes:
            return False, None
        start_times = []
        for proc in processes:
            try:
                start_times.append(proc.create_time())
            except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError, OSError):
                continue
        if not start_times:
            return True, None
        return True, datetime.fromtimestamp(min(start_times))

    def _find_processes(self, script: str) -> list[psutil.Process]:
        matches = []
        target = script.lower()
        # NOTE: On Windows, requesting "create_time" during iteration can raise
        # PermissionError for protected processes. We only need cmdline here.
        for proc in psutil.process_iter(attrs=["pid", "cmdline"], ad_value=None):
            try:
                cmdline = proc.info.get("cmdline") or []
            except (psutil.NoSuchProcess, psutil.AccessDenied, PermissionError, OSError):
                continue
            if any(target in str(part).lower() for part in cmdline):
                matches.append(proc)
        return matches

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            self.logger.warning("Failed to read tray state: %s", exc)
            return {}

    def _update_state(self, script: str, **updates: Any) -> None:
        with self.lock:
            entry = dict(self.state.get(script, {}))
            entry.update(updates)
            self.state[script] = entry
            self.state_path.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def _load_analyzer_backend(self) -> str:
        # Persisted selection wins. Fallback to env, then default.
        global_entry = self.state.get("_global", {})
        if isinstance(global_entry, dict):
            raw = global_entry.get("analyzer_backend")
            if isinstance(raw, str) and raw.strip():
                return raw.strip().lower()
        return os.getenv("ANALYZER_BACKEND", "gemini").strip().lower()

    def _set_analyzer_backend(self, backend: str) -> None:
        backend = (backend or "").strip().lower()
        if backend not in {"gemini", "local"}:
            return
        self.analyzer_backend = backend
        with self.lock:
            global_entry = self.state.get("_global")
            if not isinstance(global_entry, dict):
                global_entry = {}
            global_entry["analyzer_backend"] = backend
            self.state["_global"] = global_entry
            self.state_path.write_text(
                json.dumps(self.state, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        self.logger.info("Analyzer backend set to %s", backend)
        self._refresh_menu()


def _format_time(value: datetime | None) -> str:
    if not value:
        return "-"
    return value.strftime("%Y/%m/%d %H:%M")


def _create_icon() -> Image.Image:
    size = 64
    image = Image.new("RGB", (size, size), (20, 26, 33))
    draw = ImageDraw.Draw(image)
    draw.ellipse((6, 6, size - 6, size - 6), fill=(229, 76, 63))
    draw.rectangle((18, 18, size - 18, size - 18), fill=(255, 255, 255))
    draw.text((24, 20), "M", fill=(20, 26, 33))
    return image


def main() -> None:
    tray = TrayController()
    tray.run()


if __name__ == "__main__":
    main()
