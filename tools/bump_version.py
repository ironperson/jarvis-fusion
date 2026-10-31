#!/usr/bin/env python3
"""版本号单一事实源管理（供本地构建与 CI 共用）。

唯一事实源：``jarvis_console/__init__.py`` 里的 ``__version__``。
本脚本负责读/改它，并同步派生出两个「生成物」：

1. ``jarvis_console/platform/win/version.iss``
   Inno Setup 预处理器包含文件（MyAppVersion / MyDisplayVersion / MyOutputBase），
   避免版本号在 build.iss 里再手写一遍导致漂移。
2. ``jarvis_console/platform/win/version_info.txt``
   PyInstaller 的 Windows 版本资源（资源管理器「属性 → 详细信息」里看到的版本），
   同时为代码签名提供可核对的版本元数据。

常用：
    python tools/bump_version.py --show                     # 只看当前版本
    python tools/bump_version.py --bump patch               # 修订号 +1 并写回源码
    python tools/bump_version.py --set 2.1.0                # 指定版本并写回源码
    python tools/bump_version.py --from-tag v2.1.0          # 由 git 标签确定版本
    python tools/bump_version.py --write-inno --write-winver          # 只同步生成物
    python tools/bump_version.py --bump patch --build 42 --write-inno --write-winver --github-output

注意：``--build N``（CI 运行号）只影响安装包文件名与展示串（第四段），不改源码版本号。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PKG_INIT = REPO_ROOT / "jarvis_console" / "__init__.py"
WIN_DIR = REPO_ROOT / "jarvis_console" / "platform" / "win"
DEFAULT_INNO = WIN_DIR / "version.iss"
DEFAULT_WINVER = WIN_DIR / "version_info.txt"

_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)


class VersionError(Exception):
    """版本号格式非法或源码中找不到 ``__version__``。"""


# —— 基础读写 ——


def read_version(init_path: Path = PKG_INIT) -> str:
    """从包 ``__init__.py`` 读出当前版本号。"""
    text = init_path.read_text(encoding="utf-8")
    match = _VERSION_RE.search(text)
    if not match:
        raise VersionError(f"在 {init_path} 中找不到 __version__ = \"x.y.z\"")
    return match.group(1)


def write_version(version: str, init_path: Path = PKG_INIT) -> None:
    """把版本号写回包 ``__init__.py``（仅替换该行，保留其余内容与注释）。"""
    text = init_path.read_text(encoding="utf-8")
    if not _VERSION_RE.search(text):
        raise VersionError(f"在 {init_path} 中找不到 __version__，无法写回")
    new_text = _VERSION_RE.sub(f'__version__ = "{version}"', text, count=1)
    if new_text != text:
        init_path.write_text(new_text, encoding="utf-8")


def normalize(text: str) -> str:
    """把任意输入（可带 v 前缀）规范化为 ``X.Y.Z``；只接受 1~4 段数字。"""
    raw = text.strip().lstrip("vV")
    if not raw:
        raise VersionError("版本号为空")
    parts = raw.split(".")
    if not 1 <= len(parts) <= 4:
        raise VersionError(f"版本号段数非法（应为 1~4 段）：{text}")
    for part in parts:
        if not part.isdigit():
            raise VersionError(f"版本号含非数字段：{text}")
    nums = [int(p) for p in parts]
    while len(nums) < 3:
        nums.append(0)
    return ".".join(str(n) for n in nums[:3])


def bump(version: str, part: str) -> str:
    """按 major/minor/patch 递增，低位归零。"""
    major, minor, patch = (int(x) for x in normalize(version).split("."))
    if part == "major":
        major, minor, patch = major + 1, 0, 0
    elif part == "minor":
        minor, patch = minor + 1, 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


# —— 生成物 ——


def display_version(version: str, build: int) -> str:
    """人类可读展示串：无 CI 运行号时就是版本本身。"""
    return f"{version} (build {build})" if build > 0 else version


def output_base(version: str, build: int) -> str:
    """安装包文件名主干（避免括号等字符进入文件名）。"""
    return f"Jarvis-Setup-{version}-build{build}" if build > 0 else f"Jarvis-Setup-{version}"


def file_version(version: str, build: int) -> tuple[int, int, int, int]:
    """Windows 四段式版本号（末段用 CI 运行号，缺省 0）。"""
    major, minor, patch = (int(x) for x in normalize(version).split("."))
    return (major, minor, patch, max(build, 0))


def render_inno(version: str, build: int) -> str:
    """生成 Inno Setup 预处理器包含文件内容。"""
    return (
        "; 本文件由 tools/bump_version.py 自动生成，请勿手工编辑。\n"
        "; 重新生成：python tools/bump_version.py --write-inno\n"
        f'#define MyAppVersion "{version}"\n'
        f'#define MyBuildNumber "{max(build, 0)}"\n'
        f'#define MyDisplayVersion "{display_version(version, build)}"\n'
        f'#define MyOutputBase "{output_base(version, build)}"\n'
    )


def render_winver(version: str, build: int) -> str:
    """生成 PyInstaller 版本资源文件内容（纯 ASCII，避免编码问题）。"""
    major, minor, patch, fourth = file_version(version, build)
    full = f"{version}.{max(build, 0)}" if build > 0 else f"{version}.0"
    return f"""# UTF-8
# 本文件由 tools/bump_version.py 自动生成，请勿手工编辑。
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({major}, {minor}, {patch}, {fourth}),
    prodvers=({major}, {minor}, {patch}, {fourth}),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'JARVIS Fusion Team'),
          StringStruct('FileDescription', 'Jarvis Desktop Assistant'),
          StringStruct('FileVersion', '{full}'),
          StringStruct('InternalName', 'Jarvis'),
          StringStruct('LegalCopyright', 'GPL-3.0 (derived from GDDXX-Jarvis)'),
          StringStruct('OriginalFilename', 'Jarvis.exe'),
          StringStruct('ProductName', 'Jarvis'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write_text(path: Path, content: str) -> None:
    """写 UTF-8 文本；内容未变化时不落盘（避免无谓的 mtime 抖动）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return
    path.write_text(content, encoding="utf-8")


def emit_github_output(version: str, build: int) -> None:
    """把版本信息写入 ``$GITHUB_OUTPUT``，供后续步骤以 ``steps.version.outputs.*`` 引用。"""
    target = os.environ.get("GITHUB_OUTPUT", "").strip()
    if not target:
        print("[提示] 未设置 GITHUB_OUTPUT（非 CI 环境），跳过 github-output 写入。", file=sys.stderr)
        return
    lines = [
        f"version={version}",
        f"display={display_version(version, build)}",
        f"out_base={output_base(version, build)}",
        f"file_version={'.'.join(str(x) for x in file_version(version, build))}",
    ]
    with open(target, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


# —— CLI ——


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="读写 jarvis_console 版本号，并同步 Inno/PyInstaller 版本生成物",
    )
    parser.add_argument("--show", action="store_true", help="只打印当前版本号（纯文本，便于脚本读取）")
    parser.add_argument("--bump", choices=("major", "minor", "patch"), help="递增指定段并写回源码")
    parser.add_argument("--set", dest="set_version", metavar="X.Y.Z", help="指定版本并写回源码")
    parser.add_argument("--from-tag", metavar="TAG", help="由 git 标签（如 v2.1.0）确定版本并写回源码")
    parser.add_argument("--build", type=int, default=0, metavar="N", help="CI 运行号（作为第四段/文件名后缀）")
    parser.add_argument(
        "--write-inno",
        nargs="?",
        const=str(DEFAULT_INNO),
        default=None,
        metavar="PATH",
        help=f"生成 Inno 版本包含文件（默认 {DEFAULT_INNO.relative_to(REPO_ROOT)}）",
    )
    parser.add_argument(
        "--write-winver",
        nargs="?",
        const=str(DEFAULT_WINVER),
        default=None,
        metavar="PATH",
        help=f"生成 PyInstaller 版本资源文件（默认 {DEFAULT_WINVER.relative_to(REPO_ROOT)}）",
    )
    parser.add_argument("--github-output", action="store_true", help="把 version/display/out_base 写入 $GITHUB_OUTPUT")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要执行的动作，不写任何文件")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.build < 0:
        parser.error("--build 必须是非负整数")

    try:
        current = read_version()

        # 1) 先决定目标版本（改源码只在明确要求时才发生）
        version = current
        if args.from_tag:
            version = normalize(args.from_tag)
        elif args.set_version:
            version = normalize(args.set_version)
        elif args.bump:
            version = bump(current, args.bump)

        changed = version != current
        if args.show:
            # --show 只读：不写源码、不写生成物，输出纯版本号。
            print(current)
            return 0

        if changed:
            print(f"版本号：{current} -> {version}")
            if not args.dry_run:
                write_version(version)
        else:
            print(f"版本号：{version}（未变化）")

        # 2) 生成物
        if args.write_inno:
            content = render_inno(version, args.build)
            if args.dry_run:
                print(f"[dry-run] 将写入 {args.write_inno}：\n{content}")
            else:
                write_text(Path(args.write_inno).expanduser(), content)
                print(f"已生成 Inno 版本文件：{args.write_inno}")

        if args.write_winver:
            content = render_winver(version, args.build)
            if args.dry_run:
                print(f"[dry-run] 将写入 {args.write_winver}（{len(content)} 字符）")
            else:
                write_text(Path(args.write_winver).expanduser(), content)
                print(f"已生成 PyInstaller 版本资源：{args.write_winver}")

        # 3) CI 输出
        if args.github_output:
            if args.dry_run:
                print("[dry-run] 将写入 GITHUB_OUTPUT")
            else:
                emit_github_output(version, args.build)

        print(f"展示版本：{display_version(version, args.build)}")
        print(f"安装包名：{output_base(version, args.build)}.exe")
        return 0
    except VersionError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
