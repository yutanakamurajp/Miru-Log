from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import random
from datetime import datetime
from dataclasses import dataclass
from json import JSONDecodeError
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


def _read_tray_state(path: Path) -> dict[str, Any]:
    # JSON書き込み直後の競合を避けるため、短いリトライを入れる
    for _ in range(2):
        try:
            raw = path.read_text(encoding="utf-8")
            if not raw.strip():
                return {}
            return json.loads(raw)
        except (OSError, JSONDecodeError):
            time.sleep(0.05)
    return {}


def _write_tray_state_atomic(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    # On Windows (esp. under Dropbox sync), the target file may be temporarily locked.
    # Retry a few times rather than crashing background threads.
    for attempt in range(8):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            # Small jitter helps avoid sync races.
            time.sleep(0.05 * (attempt + 1) + random.random() * 0.02)
    # Last attempt (raise if still failing)
    os.replace(tmp, path)


class TrayController:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.logger = init_logger("tray", self.settings.log_dir, self.settings.log_level)
        self._state_path = self.settings.data_dir / STATE_FILE
        self.state: dict[str, Any] = self._load_state()
        self.analyzer_backend = self._load_analyzer_backend()
        self.processes: dict[str, subprocess.Popen] = {}
        self.lock = threading.Lock()
        self._stop_event = threading.Event()
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
        # run() blocks the main thread with the Win32 message loop, and Ctrl+C can surface as
        # an exception from a ctypes callback. Detaching keeps the UI responsive while allowing
        # graceful shutdown on KeyboardInterrupt.
        self.icon.run_detached()
        try:
            self._stop_event.wait()
        except KeyboardInterrupt:
            self.logger.info("KeyboardInterrupt received; stopping tray icon")
            try:
                self.icon.stop()
            finally:
                self._stop_event.set()

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

    def _spawn(self, script: str, args: list[str] | None = None) -> subprocess.Popen:
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        env = os.environ.copy()
        env["TRAY_STATE_PATH"] = str(self._state_path)
        argv = [sys.executable, script]
        if script == "analyzer.py":
            env["ANALYZER_BACKEND"] = self.analyzer_backend
            argv.append("--until-empty")
        if args:
            argv.extend(args)
        proc = subprocess.Popen(
            argv,
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
        self._stop_event.set()
        icon.stop()

    def _refresh_menu(self) -> None:
        try:
            self.icon.update_menu()
        except Exception:
            pass

    def _status_text(self, program: ProgramSpec) -> str:
        # Reload on demand so analyzer progress written by another process is visible.
        self.state = self._load_state()
        running, started_at = self._running_info(program.script)
        if running:
            started_text = _format_time(started_at)
            if program.script == "analyzer.py":
                entry = self._state_entry(program.script)
                if isinstance(entry, dict):
                    progress = entry.get("progress")
                    if isinstance(progress, dict):
                        processed = progress.get("processed")
                        pending = progress.get("pending")
                        last_task = progress.get("last_task")
                        if processed is not None and pending is not None:
                            tail = f"処理 {processed} / 残り {pending}"
                            if isinstance(last_task, str) and last_task.strip():
                                tail += f"・直近 {last_task.strip()}"
                            return f"状態: 実行中 ({tail})"
            return f"状態: 実行中 (開始 {started_text})"
        last_end = self._state_time(program.script, "last_end")
        last_start = self._state_time(program.script, "last_start")
        last_text = _format_time(last_end or last_start)
        if last_text == "-":
            return "状態: 停止中 (最終 -)"
        return f"状態: 停止中 (最終 {last_text})"

    def _state_entry(self, script: str) -> dict[str, Any] | None:
        entry = self.state.get(script)
        if isinstance(entry, dict):
            return entry
        scripts = self.state.get("scripts")
        if isinstance(scripts, dict):
            nested = scripts.get(script)
            if isinstance(nested, dict):
                return nested
            # analyzer.py が "analyzer" で書かれるケースも許容
            if script == "analyzer.py":
                alt = scripts.get("analyzer")
                if isinstance(alt, dict):
                    return alt
        return None

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
        if not self._state_path.exists():
            return {}
        state = _read_tray_state(self._state_path)
        if not state:
            # _read_tray_state は空/破損時に {} を返す。ログは一度だけ warning。
            try:
                raw = self._state_path.read_text(encoding="utf-8")
                if raw.strip():
                    json.loads(raw)
            except (OSError, JSONDecodeError) as exc:
                self.logger.warning("Failed to read tray state: %s", exc)
        return state

    def _update_state(self, script: str, **updates: Any) -> None:
        with self.lock:
            entry = dict(self._state_entry(script) or {})
            entry.update(updates)
            self.state[script] = entry
            # analyzer.py 側が scripts ネストに書くため、tray 側も同期しておく
            scripts = self.state.setdefault("scripts", {})
            if isinstance(scripts, dict):
                nested = dict(scripts.get(script, {})) if isinstance(scripts.get(script), dict) else {}
                nested.update(updates)
                scripts[script] = nested
            try:
                _write_tray_state_atomic(self._state_path, self.state)
            except PermissionError as exc:
                # Best-effort: avoid crashing the tray thread if the file is locked.
                self.logger.warning("Failed to update tray state (file locked): %s", exc)

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
            self._state_path.write_text(
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
