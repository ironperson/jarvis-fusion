"""可断点续传的模型下载器（HTTP Range + sha256 校验）。

设计目标（对齐融合版 JARVIS v1 架构 T14）：
- 断点续传：已下载部分以 ``.part`` 临时文件落盘，进程中断/取消后可从字节偏移继续；
- sha256 校验：manifest 给出期望值则严格校验，未给出（占位 ``__VERIFY__``）则仅计算并打印，绝不静默；
- 进度回调：按字节上报 ``(done, total, speed_bps)``，便于 CLI 或 UI 显示；
- 可取消：通过 ``cancel_cb()`` 或捕获 ``KeyboardInterrupt`` 中断，抛出 :class:`CancelRequested`；
- 优雅失败：任何下载/校验失败抛出 :class:`DownloadError`，``.manual_hint`` 给出手动放置方案，不静默吞掉。

依赖：默认仅用标准库（``urllib``）。若设置环境变量 ``JARVIS_USE_REQUESTS=1`` 且已安装
``requests``，则自动改用 ``requests``（对代理/重试更稳）；``requests`` 非强制依赖。

本文件既可被 ``first_run.py`` 导入，也可独立运行：
    python download_models.py --manifest model_manifest.json --package asr
"""
from __future__ import annotations

import bz2  # noqa: F401  (tarfile 的 'r:*' 已覆盖 .bz2，这里仅作显式依赖提示)
import hashlib
import os
import shutil
import sys
import tarfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

# —— 是否优先使用 requests（需在调用前设置或在环境变量里开启）——
PREFER_REQUESTS: bool = os.environ.get("JARVIS_USE_REQUESTS", "").strip() in {"1", "true", "yes", "on"}

# 标记“尚未核对/须用户确认”的 sha256 占位符；遇到它只打印计算值而不严格比对。
SHA256_PLACEHOLDER = "__VERIFY__"

ProgressCb = Callable[[int, int, float, str], None]  # done_bytes, total_bytes, speed_bps, package_id
CancelCb = Callable[[], bool]


class CancelRequested(Exception):
    """用户或调用方主动取消下载。"""


class DownloadError(Exception):
    """下载/校验失败；``manual_hint`` 给出可执行的手动放置说明。"""

    def __init__(self, message: str, *, manual_hint: str = "") -> None:
        super().__init__(message)
        self.manual_hint = manual_hint


def _sha256_of(
    path: Path,
    *,
    chunk_size: int = 1 << 20,
    progress_cb: Optional[ProgressCb] = None,
    cancel_cb: Optional[CancelCb] = None,
    package_id: str = "",
) -> str:
    """计算文件 sha256（流式），可选上报进度。"""
    digest = hashlib.sha256()
    total = path.stat().st_size
    done = 0
    last_t = time.monotonic()
    with open(path, "rb") as handle:
        while True:
            if cancel_cb and cancel_cb():
                raise CancelRequested("校验阶段被取消")
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
            done += len(chunk)
            now = time.monotonic()
            if progress_cb and now - last_t >= 0.25:
                speed = (done - 0) / max(now - last_t, 1e-6)
                progress_cb(done, total, speed, package_id)
                last_t = now
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> tuple[bool, str]:
    """返回 ``(是否匹配, 实际sha256)``；``expected`` 为空或占位时视为“仅需计算”。"""
    actual = _sha256_of(path)
    if not expected or expected == SHA256_PLACEHOLDER:
        return (True, actual)  # 占位：不强制比对，只回传实际值供记录
    return (actual.lower() == expected.lower().strip(), actual)


def _common_top_dir(names: list[str]) -> Optional[str]:
    """若所有条目共享同一个顶级目录，返回它；否则返回 ``None``。"""
    tops: set[str] = set()
    for name in names:
        cleaned = name.strip("/")
        if not cleaned:
            continue
        top = cleaned.split("/", 1)[0]
        if top:
            tops.add(top)
        else:
            return None
    if len(tops) == 1:
        return next(iter(tops))
    return None


def _strip_top(name: str, top: Optional[str]) -> str:
    if not top:
        return name.lstrip("/")
    if name == top or name == top + "/":
        return ""
    if name.startswith(top + "/"):
        return name[len(top) + 1 :].lstrip("/")
    return name.lstrip("/")


def extract_archive(archive_path: Path, target_dir: Path, *, flatten: bool = True) -> list[str]:
    """解压 ``.zip`` / ``.tar`` / ``.tar.gz`` / ``.tar.bz2`` 到 ``target_dir``。

    ``flatten=True`` 时，若压缩包内所有文件都在同一个顶级目录下，则去掉该顶级目录，
    直接把内容铺平到 ``target_dir``（sherpa-onnx 发布的模型包普遍如此）。返回解出的文件相对列表。
    """
    archive_path = Path(archive_path)
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    extracted: list[str] = []

    is_zip = archive_path.suffix.lower() == ".zip" or zipfile.is_zipfile(str(archive_path))
    if is_zip:
        with zipfile.ZipFile(archive_path) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            top = _common_top_dir(zf.namelist()) if flatten else None
            for name in names:
                rel = _strip_top(name, top)
                if not rel:
                    continue
                dest = target_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(name) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
                extracted.append(rel)
    else:
        with tarfile.open(archive_path, "r:*") as tf:
            members = [m for m in tf.getmembers() if m.isfile()]
            top = _common_top_dir([m.name for m in members]) if flatten else None
            for m in members:
                rel = _strip_top(m.name, top)
                if not rel:
                    continue
                dest = target_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                with tf.extractfile(m) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
                extracted.append(rel)
    return extracted


def _open_response(url: str, headers: dict[str, str], timeout: int):
    if PREFER_REQUESTS:
        import requests  # type: ignore

        return requests.get(url, headers=headers, stream=True, timeout=timeout)
    import urllib.request

    request = urllib.request.Request(url, headers=headers)
    return urllib.request.urlopen(request, timeout=timeout)


def _response_length(resp, prefer_requests: bool) -> int:
    value = resp.headers.get("Content-Length") or resp.headers.get("content-length") or "0"
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _iter_chunks(resp, chunk_size: int, prefer_requests: bool):
    if prefer_requests:
        for chunk in resp.iter_content(chunk_size):
            if chunk:
                yield chunk
    else:
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            yield chunk


def download_file(
    url: str,
    dest: Path,
    *,
    expected_sha256: Optional[str] = None,
    chunk_size: int = 1 << 16,
    timeout: int = 30,
    progress_cb: Optional[ProgressCb] = None,
    cancel_cb: Optional[CancelCb] = None,
    package_id: str = "",
) -> str:
    """下载 ``url`` 到 ``dest``（原子完成：先写 ``.part``，校验通过后改名）。

    支持 HTTP Range 断点续传：若 ``dest.part`` 已存在，则从该字节偏移继续。
    返回实际计算出的 sha256。校验失败抛出 :class:`DownloadError`。
    """
    dest = Path(dest)
    part = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)

    resume_offset = part.stat().st_size if part.is_file() else 0
    headers: dict[str, str] = {}
    if resume_offset > 0:
        headers["Range"] = f"bytes={resume_offset}-"

    try:
        resp = _open_response(url, headers, timeout)
    except Exception as exc:  # noqa: BLE001 - 网络/地址错误统一为可处理的下载错误
        raise DownloadError(
            f"无法连接模型源（{url}）：{exc}",
            manual_hint=(
                f"请检查网络后重试；或手动下载该文件到：\n  {dest}\n"
                f"然后将同名文件放好后重新运行本向导（支持断点续传）。"
            ),
        ) from exc

    prefer_requests = PREFER_REQUESTS
    status = getattr(resp, "status_code", 200)
    if status == 416:  # 请求范围越界：服务端已无更多内容，重新从头下
        resume_offset = 0
        if part.is_file():
            part.unlink()
    elif status not in (200, 206):
        raise DownloadError(
            f"模型源返回非预期状态码 {status}（{url}）",
            manual_hint=f"请确认 URL 有效并可公开访问，或手动下载到：\n  {dest}",
        )

    total = _response_length(resp, prefer_requests)
    # Range 续传时，Content-Length 为“剩余”字节；换算成整体总量。
    overall_total = (resume_offset + total) if total > 0 else 0

    mode = "ab" if resume_offset > 0 and status == 206 else "wb"
    if mode == "wb" and part.is_file():
        part.unlink()

    written = resume_offset
    start_t = time.monotonic()
    last_report = start_t
    try:
        with open(part, mode) as handle:
            for chunk in _iter_chunks(resp, chunk_size, prefer_requests):
                if cancel_cb and cancel_cb():
                    raise CancelRequested(f"下载 {package_id} 被取消")
                handle.write(chunk)
                written += len(chunk)
                now = time.monotonic()
                if progress_cb and now - last_report >= 0.2:
                    elapsed = max(now - start_t, 1e-6)
                    speed = (written - resume_offset) / elapsed
                    progress_cb(written, overall_total, speed, package_id)
                    last_report = now
        if progress_cb:
            progress_cb(written, overall_total, 0.0, package_id)
    except CancelRequested:
        # 保留 .part 以便续传，不删除。
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass
        raise
    except Exception as exc:  # noqa: BLE001
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass
        raise DownloadError(
            f"下载中断（{url}）：{exc}",
            manual_hint=f"已下载部分保留在 {part}，可重新运行本向导续传。",
        ) from exc

    try:
        resp.close()
    except Exception:  # noqa: BLE001
        pass

    ok, actual = verify_sha256(part, expected_sha256 or "")
    if expected_sha256 and expected_sha256 != SHA256_PLACEHOLDER and not ok:
        raise DownloadError(
            f"sha256 校验失败：期望 {expected_sha256}，实际 {actual}",
            manual_hint=(
                f"文件可能损坏或被替换。请删除 {part} 后重试；\n"
                f"或核对 manifest 中的 sha256 是否为该文件正确值。"
            ),
        )
    part.replace(dest)
    return actual


# —— manifest 数据结构 ——


@dataclass
class ManifestFile:
    """manifest 中单个文件条目。"""

    name: str
    url: str = ""
    sha256: str = SHA256_PLACEHOLDER
    size_mb: float = 0.0


@dataclass
class ManifestPackage:
    """manifest 中一个模型包（对应一个模型目录）。"""

    id: str
    purpose: str
    dest: str  # 相对 MODELS_DIR 的子目录
    expected: list[str] = field(default_factory=list)  # 用于“是否就绪”判定的文件名
    files: list[ManifestFile] = field(default_factory=list)
    archive: Optional[ManifestFile] = None  # 若为压缩包模式，则下载并解压
    required: bool = True
    verify_note: str = ""
    post_extract: str = ""  # 解压后的后处理钩子名（目前支持 "wakeword"）


@dataclass
class ModelManifest:
    """解析后的模型清单。"""

    version: int = 1
    sha256_verified: bool = False
    notes: str = ""
    packages: list[ManifestPackage] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path) -> "ModelManifest":
        import json

        data = json.loads(Path(path).read_text(encoding="utf-8"))
        packages: list[ManifestPackage] = []
        for raw in data.get("packages", []):
            files = [ManifestFile(**f) for f in raw.get("files", [])]
            archive = ManifestFile(**raw["archive"]) if raw.get("archive") else None
            packages.append(
                ManifestPackage(
                    id=raw["id"],
                    purpose=raw.get("purpose", ""),
                    dest=raw.get("dest", raw["id"]),
                    expected=raw.get("expected", []),
                    files=files,
                    archive=archive,
                    required=raw.get("required", True),
                    verify_note=raw.get("verify_note", ""),
                    post_extract=raw.get("post_extract", ""),
                )
            )
        return cls(
            version=data.get("version", 1),
            sha256_verified=bool(data.get("sha256_verified", False)),
            notes=data.get("notes", ""),
            packages=packages,
        )


@dataclass
class PackageStatus:
    """某个模型包在当前本地的就绪状态。"""

    package: ManifestPackage
    present: bool
    downloadable: bool  # 是否有可自动下载的 URL
    missing_files: list[str] = field(default_factory=list)


class ModelDownloader:
    """依据 manifest 在 ``models_root`` 下下载/校验模型包。"""

    def __init__(self, manifest: ModelManifest, models_root: Path) -> None:
        self.manifest = manifest
        self.models_root = Path(models_root)

    # —— 就绪判定 ——
    def is_package_present(self, pkg: ManifestPackage) -> tuple[bool, list[str]]:
        dest = self.models_root / pkg.dest
        missing: list[str] = []
        for name in pkg.expected:
            if not (dest / name).is_file():
                missing.append(name)
        return (len(missing) == 0), missing

    def status(self) -> list[PackageStatus]:
        result: list[PackageStatus] = []
        for pkg in self.manifest.packages:
            present, missing = self.is_package_present(pkg)
            downloadable = bool(pkg.archive and pkg.archive.url) or any(f.url for f in pkg.files)
            result.append(PackageStatus(pkg, present, downloadable, missing))
        return result

    def _has_any_url(self, pkg: ManifestPackage) -> bool:
        if pkg.archive and pkg.archive.url:
            return True
        return any(f.url for f in pkg.files)

    @staticmethod
    def _run_post_extract(pkg: ManifestPackage, dest: Path) -> None:
        """解压后的后处理钩子（manifest 的 ``post_extract`` 字段）。

        目前支持：
        - ``"wakeword"``：把官方 KWS 包的长命名 onnx 归一成 encoder/decoder/joiner.onnx
          （复用 ``jarvis_console.speech.wake.normalize_model_dir``）。
        钩子失败只提示、不抛错：不影响已下载的模型文件本身。
        """
        if not pkg.post_extract:
            return
        if pkg.post_extract != "wakeword":
            print(f"    [提示] 未知 post_extract 钩子：{pkg.post_extract}，跳过。")
            return
        try:
            from jarvis_console.speech.wake import normalize_model_dir

            for item in normalize_model_dir(dest):
                print(f"    归一化模型文件名：{item}")
        except Exception as exc:  # noqa: BLE001 - 钩子失败不应阻断下载流程
            print(f"    [提示] 未能自动归一化模型文件名（{exc}）；"
                  f"可运行 python tools/kws_setup.py --models-dir {dest} 修复。")

    # —— 下载单个包 ——
    def download_package(
        self,
        pkg: ManifestPackage,
        *,
        progress_cb: Optional[ProgressCb] = None,
        cancel_cb: Optional[CancelCb] = None,
    ) -> str:
        """下载并落地一个包；返回简短结果描述。"""
        dest = self.models_root / pkg.dest
        if pkg.archive and pkg.archive.url:
            cache_dir = self.models_root / ".cache"
            cache_dir.mkdir(parents=True, exist_ok=True)
            archive_path = cache_dir / f"{pkg.id}{Path(pkg.archive.url).suffix or '.bin'}"
            actual = download_file(
                pkg.archive.url,
                archive_path,
                expected_sha256=pkg.archive.sha256,
                progress_cb=progress_cb,
                cancel_cb=cancel_cb,
                package_id=pkg.id,
            )
            extract_archive(archive_path, dest, flatten=True)
            try:
                archive_path.unlink()
            except OSError:
                pass
            self._run_post_extract(pkg, dest)
            return f"{pkg.id}: 解压完成（sha256={actual[:16]}…）"
        # 多文件模式
        results: list[str] = []
        for f in pkg.files:
            if not f.url:
                continue
            target = dest / f.name
            actual = download_file(
                f.url,
                target,
                expected_sha256=f.sha256,
                progress_cb=progress_cb,
                cancel_cb=cancel_cb,
                package_id=pkg.id,
            )
            results.append(f"{f.name}={actual[:16]}…")
        return f"{pkg.id}: " + ", ".join(results)

    # —— 批量下载 ——
    def run(        self,
        package_ids: list[str],
        *,
        progress_cb: Optional[ProgressCb] = None,
        cancel_cb: Optional[CancelCb] = None,
        per_package_cb: Optional[Callable[[str, str], None]] = None,
    ) -> dict[str, str]:
        """下载 ``package_ids`` 列出的包；返回 ``{id: 结果描述}``。"""
        by_id = {p.id: p for p in self.manifest.packages}
        outcomes: dict[str, str] = {}
        for pid in package_ids:
            pkg = by_id.get(pid)
            if pkg is None:
                outcomes[pid] = "manifest 中无此包，跳过"
                continue
            if not self._has_any_url(pkg):
                outcomes[pid] = "无可用 URL（需手动放置），跳过自动下载"
                if per_package_cb:
                    per_package_cb(pid, outcomes[pid])
                continue
            try:
                msg = self.download_package(pkg, progress_cb=progress_cb, cancel_cb=cancel_cb)
                outcomes[pid] = msg
            except CancelRequested as exc:
                outcomes[pid] = f"已取消：{exc}"
                if per_package_cb:
                    per_package_cb(pid, outcomes[pid])
                raise
            except DownloadError as exc:
                outcomes[pid] = f"失败：{exc}"
                if per_package_cb:
                    per_package_cb(pid, outcomes[pid])
                raise
            if per_package_cb:
                per_package_cb(pid, msg)
        return outcomes


def _ensure_repo_on_path() -> None:
    """把可找到 ``jarvis_console`` 包的目录加入 sys.path（兼容源码与冻结两种运行环境）。"""
    here = Path(__file__).resolve().parent
    candidates = [
        here.parent.parent,  # 源码树：platform/win -> 仓库根（含 jarvis_console）
        here / "_internal",  # 冻结：脚本随包置于 jarvis_console/_packaging
        here.parent / "_internal",  # 冻结：脚本置于安装根
        here.parent.parent / "_internal",
        here,
    ]
    seen: set[str] = set()
    for cand in candidates:
        cand = cand.resolve()
        if str(cand) in seen:
            continue
        seen.add(str(cand))
        if (cand / "jarvis_console").is_dir():
            if str(cand) not in sys.path:
                sys.path.insert(0, str(cand))


def _build_cli() -> tuple[Path, list[str], bool]:
    import argparse

    parser = argparse.ArgumentParser(description="JARVIS 模型断点续传下载器")
    default_manifest = Path(__file__).resolve().parent / "model_manifest.json"
    parser.add_argument("--manifest", default=str(default_manifest), help="模型清单 JSON 路径")
    parser.add_argument("--package", action="append", default=[], help="指定下载的包 id（可重复）")
    parser.add_argument("--models-root", default="", help="覆盖模型根目录（默认跟随 jarvis_console.paths）")
    parser.add_argument("--use-requests", action="store_true", help="强制使用 requests 而非 urllib")
    args = parser.parse_args()

    global PREFER_REQUESTS
    if args.use_requests:
        PREFER_REQUESTS = True

    manifest_path = Path(args.manifest)
    return manifest_path, args.package, bool(args.models_root), args.models_root  # type: ignore


def main() -> int:
    manifest_path, packages, has_root, root_override = _build_cli()
    _ensure_repo_on_path()

    from jarvis_console.paths import MODELS_DIR  # 复用统一路径常量，避免硬编码

    models_root = Path(root_override) if has_root else MODELS_DIR
    manifest = ModelManifest.load(manifest_path)
    downloader = ModelDownloader(manifest, models_root)

    to_do = packages if packages else [p.id for p in manifest.packages]
    print(f"模型根目录：{models_root}")
    print(f"待处理包：{', '.join(to_do)}")

    try:
        outcomes = downloader.run(to_do, progress_cb=_cli_progress)
    except CancelRequested as exc:
        print(f"\n已取消：{exc}")
        return 130
    except DownloadError as exc:
        print(f"\n下载失败：{exc}")
        if exc.manual_hint:
            print("手动方案：\n" + exc.manual_hint)
        return 1

    print("\n完成：")
    for pid, msg in outcomes.items():
        print(f"  - {pid}: {msg}")
    return 0


def _cli_progress(done: int, total: int, speed: float, package_id: str) -> None:
    pct = (done / total * 100) if total > 0 else 0.0
    mb_done = done / (1024 * 1024)
    mb_total = total / (1024 * 1024) if total > 0 else 0.0
    mbps = speed / (1024 * 1024)
    line = f"\r[{package_id}] {pct:5.1f}%  {mb_done:6.1f}/{mb_total:6.1f} MB  {mbps:5.2f} MB/s"
    sys.stderr.write(line)
    sys.stderr.flush()


if __name__ == "__main__":
    raise SystemExit(main())
