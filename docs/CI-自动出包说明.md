# 安装包 CI 自动出包说明

本仓库提供 GitHub Actions 工作流 `.github/workflows/build-installer.yml`，在 Windows runner 上自动：
1. 装依赖（核心依赖 + 尽力安装语音依赖）；
2. 解析版本号（标签取标签 / 手动可指定 / 默认自动递增 patch）；
3. `pyinstaller` 打包成 `dist\Jarvis\Jarvis.exe`（onedir，带 Windows 版本资源）；
4. 可选代码签名（配置证书 Secret 时对 exe 与安装包签名 + 校验）；
5. `Inno Setup 6` 编译安装包；
6. 上传产物；若是 `v*` 标签，自动挂到 GitHub Release。

---

## 一、前置：把项目推到 GitHub

当前工作区**还不是 git 仓库**。首次启用 CI：

```bash
cd "D:/workbuddy worktool/贾维斯"
git init
git add .
git commit -m "feat: 融合版 JARVIS v1 + 打包与 CI"
# 在 GitHub 新建空仓库后：
git remote add origin https://github.com/<你的账号>/<仓库名>.git
git branch -M main
git push -u origin main
```

> 仓库根已提供 `.gitignore`，会忽略 `.venv/`、`dist/`、`.build/`、`installer/`、`jarvis_console/models/` 等大文件，避免把 ~GB 模型/构建产物推上去。

---

## 二、版本号：一处维护，自动递增

版本号唯一事实源是 `jarvis_console/__init__.py` 的 `__version__`；`tools/bump_version.py` 负责派生
`jarvis_console/platform/win/version.iss`（安装包版本）与 `version_info.txt`（exe 版本资源）。

| 触发方式 | 版本号来源 | 安装包名 |
|---|---|---|
| 推送标签 `v2.1.0` | 标签本身（权威） | `Jarvis-Setup-2.1.0.exe` |
| 手动运行 + 填 `version` 输入 | 你填的值 | `Jarvis-Setup-<你填的版本>.exe` |
| 手动运行、不填 | 当前 patch +1，第四段 = CI 运行号 | `Jarvis-Setup-2.0.1-build42.exe` |

本地改版本（只改一处，两个生成物同步刷新）：

```bat
python tools\bump_version.py --bump patch    REM 1.2.3 -> 1.2.4
python tools\bump_version.py --set 2.1.0
python tools\bump_version.py --show          REM 只看当前版本（纯文本，脚本可用）
```

发新版推荐流程：

```bash
python tools/bump_version.py --bump minor      # 或 --set 2.1.0
git add -A && git commit -m "chore: bump to 2.1.0"
git tag v2.1.0 && git push origin v2.1.0       # 标签触发，CI 按标签版本出包并建 Release
```

---

## 三、代码签名（可选）

Windows 对未签名安装包会弹 SmartScreen「已保护你的电脑」。配置证书后 CI 自动签名并校验：

| Secret | 内容 |
|---|---|
| `WINDOWS_CERT_PFX_BASE64` | `.pfx` 证书的 base64 文本 |
| `WINDOWS_CERT_PASSWORD` | 该 `.pfx` 的密码（无密码可留空字符串） |

生成 base64（PowerShell）：

```powershell
[Convert]::ToBase64String([IO.File]::ReadAllBytes("C:\path\to\jarvis-sign.pfx"))
```

签名行为（`signtool`，RFC 3161 时间戳）：

- 对 `dist\Jarvis\Jarvis.exe` 签名 → `verify /pa` 校验；
- 对 `installer\Jarvis-Setup-*.exe` 签名 → 校验；
- 未配置 Secret：跳过签名，工作流输出 notice 告知「本次产物未签名」，**不阻断出包**。

本地签名：

```bat
signtool sign /fd sha256 /td sha256 /tr http://timestamp.digicert.com /f "证书.pfx" /p "密码" installer\Jarvis-Setup-2.1.0.exe
```

> 证书来源：代码签名证书（EV/OV）可找 CA 或云厂商购买；也可用 Azure Trusted Signing（需改工作流，本文档未展开）。

---

## 四、触发方式

| 方式 | 操作 | 结果 |
|---|---|---|
| 手动 | GitHub → Actions → `build-installer` → Run workflow | 只出产物（版本自动递增），不发 Release |
| 手动 + 指定版本 | Run workflow 时填 `version` | 同上，但版本是你填的 |
| 打标签 | `git tag v2.1.0 && git push origin v2.1.0` | 出包 + 自动创建 Release 并附安装包 |

---

## 五、产物在哪

- Actions 运行页 → 该次 run → **Artifacts**（名称带版本号）：
  - `Jarvis-Setup-<版本>`：安装包 `.exe`
  - `Jarvis-portable-<版本>`：免安装目录 `dist\Jarvis\`
- 若是标签触发：仓库 **Releases** 页面直接下载，Release 名形如 `Jarvis 2.1.0`。

---

## 六、CI 关键点与坑

1. **语音依赖尽力安装**：`sounddevice/webrtcvad/resemblyzer` 在 CI 上可能因编译/依赖较大失败，工作流对这一步 `continue-on-error`，不阻断出包。exe 在缺语音依赖时会优雅降级（纯文字 + HUD 仍可用）。
2. **Inno Setup**：runner 用 `choco install innosetup` 安装，`ISCC.exe` 固定路径 `C:\Program Files (x86)\Inno Setup 6\ISCC.exe`。
3. **版本号解析**在 PyInstaller 之前执行：`bump_version.py` 会把 `version.iss`/`version_info.txt` 写进工作区，保证 exe 属性里的版本与安装包一致。
4. **签名条件判断**：GitHub 不允许在 `if:` 里直接读 Secret，故用 workflow 级 `env: SIGN_PFX_BASE64: ${{ secrets.WINDOWS_CERT_PFX_BASE64 }}` 中转，步骤用 `if: env.SIGN_PFX_BASE64 != ''` 判断——fork 出来的仓库没配 Secret 也能正常出包。
5. **模型不打包**：~GB 语音模型不进安装包，首启由 `first_run.py` / `download_models.py` 断点续传下载；唤醒词还可用 `tools\kws_setup.py` 一键配置（见 `docs/唤醒词自训教程.md` 第 1.5 节）。
6. **标签必须形如 `vX.Y.Z`**：`--from-tag` 会去掉 `v` 前缀并要求数字段，`v2.1.0-rc1` 这类预发布标签会直接报错（版本号只接受数字段）。

---

## 七、本地等价命令（无 CI 时）

```bat
python tools\bump_version.py --write-inno --write-winver
python -m PyInstaller jarvis_console\platform\win\jarvis.spec --noconfirm --clean --distpath dist --workpath .build
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" jarvis_console\platform\win\build.iss
```

或直接双击 `jarvis_console\platform\win\build.bat`（三步全含，并按当前版本打印安装包名）。
