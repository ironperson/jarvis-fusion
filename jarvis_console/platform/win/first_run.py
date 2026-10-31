"""首次启动模型下载引导（JARVIS v1 · T14）。

职责：
1. 依据统一路径常量（jarvis_console.paths）检测缺失的语音模型；
2. 提供进度 / 取消的引导（默认 CLI；若 PySide6 可用且未禁用，可用 --gui 走简单进度窗）；
3. 调用 download_models 做断点续传 + sha256 校验；
4. 完成后写标记文件避免重复引导；
5. 无网络 / 无可用 URL 时，打印精确的手动放置说明，绝不静默失败。

运行方式：
    python first_run.py                 # 交互式 CLI 引导
    python first_run.py --yes           # 非交互，直接开始下载
    python first_run.py --check         # 仅报告缺失情况，不下载
    python first_run.py --gui           # 若可用，使用 PySide6 进度窗
    python first_run.py --only asr      # 只下载指定包

说明：本脚本是 T12 应用内 SetupWizard 的「命令行等价物」。最终用户双击
Jarvis.exe 后由应用内向导引导；本脚本面向开发者 / 高级用户 / 无网络手动放置场景。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

from download_models import (
    CancelRequested,
    DownloadError,
    ModelDownloader,
    ModelManifest,
    _ensure_repo_on_path,
)

MARKER_NAME = "first_run_done.json"


def _stdout_utf8() -> None:
    """在 Windows 上强制 stderr/stdout 用 UTF-8，避免中文乱码。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass


def _marker_path() -> Path:
    from jarvis_console.paths import HOME_JARVIS

    return Path(HOME_JARVIS) / MARKER_NAME


def _write_marker(models_root: Path, outcomes: dict[str, str]) -> None:
    marker = _marker_path()
    try:
        marker.write_text(
            json.dumps(
                {
                    "done": True,
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "models_root": str(models_root),
                    "outcomes": outcomes,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[警告] 标记文件写入失败（不影响模型）：{exc}")


def _read_marker() -> Optional[dict]:
    marker = _marker_path()
    if marker.is_file():
        try:
            return json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return None


def _print_status(downloader: ModelDownloader) -> list:
    print("\n=== 模型就绪状态 ===")
    to_download: list[str] = []
    manual: list[str] = []
    for st in downloader.status():
        pkg = st.package
        if st.present:
            print(f"  [OK]   {pkg.id:<10} {pkg.purpose}")
        elif st.downloadable:
            print(f"  [下载] {pkg.id:<10} {pkg.purpose}（缺失：{', '.join(st.missing_files)}）")
            to_download.append(pkg.id)
        else:
            flag = "（必需）" if pkg.required else "（可选）"
            print(f"  [手动] {pkg.id:<10} {pkg.purpose}{flag} —— 无可用 URL，须手动放置")
            manual.append(pkg.id)
    return to_download, manual


def _print_manual_guide(downloader: ModelDownloader, manual_ids: list[str]) -> None:
    if not manual_ids:
        return
    print("\n=== 需要手动放置的模型 ===")
    for st in downloader.status():
        if st.package.id not in manual_ids:
            continue
        pkg = st.package
        dest = downloader.models_root / pkg.dest
        print(f"\n● {pkg.id}：请把以下文件放到目录  {dest}")
        for name in pkg.expected:
            print(f"    - {name}")
        if pkg.verify_note:
            print(f"  说明：{pkg.verify_note}")


def _cli_progress(done: int, total: int, speed: float, package_id: str) -> None:
    pct = (done / total * 100) if total > 0 else 0.0
    mb_done = done / (1024 * 1024)
    mb_total = total / (1024 * 1024) if total > 0 else 0.0
    mbps = speed / (1024 * 1024)
    sys.stderr.write(f"\r[{package_id}] {pct:5.1f}%  {mb_done:6.1f}/{mb_total:6.1f} MB  {mbps:5.2f} MB/s")
    sys.stderr.flush()


def _run_cli(downloader: ModelDownloader, to_download: list[str], auto: bool) -> int:
    if not to_download:
        print("\n没有需要下载的模型包。")
        return 0
    total_size = sum(
        p.archive.size_mb if p.archive else sum(f.size_mb for f in p.files)
        for p in downloader.manifest.packages
        if p.id in to_download
    )
    print(f"\n即将下载 {len(to_download)} 个包（约 {total_size:.0f} MB，支持断点续传）。")
    if not auto:
        answer = input("是否开始下载？(Y/n，Ctrl+C 可随时取消) ").strip().lower()
        if answer not in {"", "y", "yes"}:
            print("已跳过下载。你之后可手动放置模型，或重新运行本向导。")
            return 0
    print("开始下载（Ctrl+C 取消，已下载部分会保留以便续传）……\n")
    try:
        outcomes = downloader.run(to_download, progress_cb=_cli_progress)
    except KeyboardInterrupt:
        print("\n\n已取消。已下载的部分保留在磁盘，下次运行会续传。")
        return 130
    except CancelRequested as exc:
        print(f"\n\n已取消：{exc}")
        return 130
    except DownloadError as exc:
        print(f"\n\n下载失败：{exc}")
        if exc.manual_hint:
            print("手动方案：\n" + exc.manual_hint)
        return 1
    sys.stderr.write("\n")
    print("下载结果：")
    for pid, msg in outcomes.items():
        print(f"  - {pid}: {msg}")
    return 0


def _run_gui(downloader: ModelDownloader, to_download: list[str], auto: bool) -> int:
    """可选的 PySide6 进度窗；失败则回退到 CLI。"""
    try:
        from PySide6.QtCore import QThread, Signal
        from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog
    except Exception as exc:  # noqa: BLE001
        print(f"[提示] PySide6 不可用，回退到命令行引导：{exc}")
        return _run_cli(downloader, to_download, auto)

    class _Worker(QThread):
        progress = Signal(int, int, str)  # done, total, package_id
        finished_pkg = Signal(str, str)
        failed = Signal(str)
        cancelled = Signal()

        def __init__(self, downloader: ModelDownloader, ids: list[str]) -> None:
            super().__init__()
            self._dl = downloader
            self._ids = ids
            self._cancel = False

        def request_cancel(self) -> None:
            self._cancel = True

        def run(self) -> None:  # noqa: D401
            def cb(done: int, total: int, _speed: float, pid: str) -> None:
                self.progress.emit(done, total, pid)

            def cc() -> bool:
                return self._cancel

            try:
                self._dl.run(self._ids, progress_cb=cb, cancel_cb=cc, per_package_cb=lambda i, m: self.finished_pkg.emit(i, m))
            except CancelRequested:
                self.cancelled.emit()
            except DownloadError as exc:
                self.failed.emit(str(exc))

    app = QApplication.instance() or QApplication(sys.argv)
    dlg = QProgressDialog("正在下载 JARVIS 语音模型…", "取消", 0, 100)
    dlg.setWindowTitle("JARVIS 首启下载")
    dlg.setMinimumDuration(0)
    dlg.setValue(0)
    dlg.show()

    worker = _Worker(downloader, to_download)
    worker.progress.connect(lambda d, t, _p: dlg.setValue(int(d / t * 100)) if t > 0 else None)
    worker.finished_pkg.connect(lambda i, m: print(f"  - {i}: {m}"))
    worker.cancelled.connect(lambda: QMessageBox.information(dlg, "已取消", "下载已取消，已下载部分保留以便续传。"))
    worker.failed.connect(lambda e: QMessageBox.critical(dlg, "下载失败", e))
    dlg.canceled.connect(worker.request_cancel)
    worker.start()
    while worker.isRunning():
        app.processEvents()
        time.sleep(0.05)
    dlg.reset()
    return 0


def main() -> int:
    _stdout_utf8()
    _ensure_repo_on_path()

    import argparse

    parser = argparse.ArgumentParser(description="JARVIS 首启模型下载引导")
    default_manifest = Path(__file__).resolve().parent / "model_manifest.json"
    parser.add_argument("--manifest", default=str(default_manifest))
    parser.add_argument("--models-root", default="", help="覆盖模型根目录")
    parser.add_argument("--yes", action="store_true", help="非交互，直接下载")
    parser.add_argument("--check", action="store_true", help="仅报告缺失，不下载")
    parser.add_argument("--gui", action="store_true", help="优先用 PySide6 进度窗")
    parser.add_argument("--only", action="append", default=[], help="只处理指定包 id")
    args = parser.parse_args()

    from jarvis_console.paths import MODELS_DIR

    models_root = Path(args.models_root) if args.models_root else MODELS_DIR
    manifest = ModelManifest.load(Path(args.manifest))
    downloader = ModelDownloader(manifest, models_root)

    # 标记文件：曾成功完成则快速复核即可，避免重复打扰。
    marker = _read_marker()
    to_download, manual = _print_status(downloader)

    if not to_download and not manual:
        print("\n所有模型已就绪，无需下载。")
        if marker is None:
            _write_marker(models_root, {})
        return 0

    _print_manual_guide(downloader, manual)

    if args.only:
        to_download = [pid for pid in to_download if pid in args.only]
        if not to_download:
            print("\n--only 指定的包均已就绪或不可自动下载。")
            return 0

    if args.check:
        print("\n（--check 模式：未执行下载。）")
        return 0 if not to_download else 1

    if args.gui:
        code = _run_gui(downloader, to_download, args.yes)
    else:
        code = _run_cli(downloader, to_download, args.yes)

    if code == 0:
        # 重新校验：下载完成且手动项已就位才算真正就绪。
        still_missing = [st.package.id for st in downloader.status() if not st.present and st.package.required]
        if not still_missing:
            _write_marker(models_root, {"auto_downloaded": to_download})
            print("\n✅ 所需模型已全部就绪，可启动 Jarvis.exe。")
        else:
            print(f"\n以下必需模型仍缺失（可能需手动放置）：{', '.join(still_missing)}")
            print(f"放置目录根：{models_root}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
