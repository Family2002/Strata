#!/usr/bin/env python3
"""Build the Windows offline installer on a Linux machine.

It downloads pinned upstream files, checks their SHA-256, and packs them with makensis.
The target PC does not run this script. No model weights are included.
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tarfile
import textwrap
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packaging"))

import node_runtime  # noqa: E402
from strata_control.windows_offline import preflight  # noqa: E402

CACHE = Path("/tmp/strata-offline-cache")
STAGE = Path("/tmp/strata-offline-stage")
OUT_DIR = Path("/opt/cursor/artifacts")

PINS = {
    "node.zip": (
        "https://nodejs.org/dist/v24.12.0/node-v24.12.0-win-x64.zip",
        "9c125f61ae947b52e779095830f9cac267846a043ef7192183c84016aaad2812",
    ),
    "control.zip": (
        "https://github.com/Niko1221/Strata/releases/download/v0.2.9/strata-control-0.1.55-windows-x86_64.zip",
        "bdca3a42071704eab33455a4c85d4867c0ecfd6face95a08f13e8967e5594e99",
    ),
    "engine-cuda12.tar.gz": (
        "https://github.com/Niko1221/Strata/releases/download/v0.2.9/strata-engine-0.1.55-windows-x86_64-cuda-cuda12.tar.gz",
        "fd3d3ff7f875d1878e88c69a35746373d5cf522e7cf5d5d3f8ba8eeff09e7739",
    ),
    "engine-cuda13.tar.gz": (
        "https://github.com/Niko1221/Strata/releases/download/v0.2.9/strata-engine-0.1.55-windows-x86_64-cuda-cuda13.tar.gz",
        "34f399a0a22e4af8b434439de04dcce3734ccba7e884ef6003439ba148d667df",
    ),
    "llama.zip": (
        "https://github.com/ggml-org/llama.cpp/archive/3cf03257f219afbe7334045ff7c6a06ac68c627d.zip",
        "cbe23c594282ead2937abb3f008e51fcec4609d9256652fff42c7cc1c21ea47b",
    ),
}

ENGINE_DIRS = {
    "engine-cuda12.tar.gz": "0.1.55-windows-x86_64-cuda-cuda12",
    "engine-cuda13.tar.gz": "0.1.55-windows-x86_64-cuda-cuda13",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(name: str) -> Path:
    url, want = PINS[name]
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / name
    if dest.is_file() and sha256(dest) == want:
        print(f"cached {name}")
        return dest
    print(f"downloading {url}")
    part = dest.with_suffix(dest.suffix + ".part")
    subprocess.run(["curl", "-fL", "--retry", "3", "-o", str(part), url], check=True)
    got = sha256(part)
    if got != want:
        part.unlink(missing_ok=True)
        raise SystemExit(f"{name} hash {got} != {want}")
    part.replace(dest)
    return dest


def copy_tree(src: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.map"))


def main() -> int:
    payload = STAGE / "payload"
    if payload.exists():
        shutil.rmtree(payload)
    payload.mkdir(parents=True)

    node_runtime.extract(fetch("node.zip"), payload / "node")
    with zipfile.ZipFile(fetch("control.zip")) as archive:
        archive.extractall(payload / "control")
    for name in ("strata.py", "setup.py"):
        shutil.copy2(ROOT / name, payload / "control" / name)
    for name in ("strata_cli", "strata_control"):
        copy_tree(ROOT / name, payload / "control" / name)
    shutil.copy2(ROOT / "tools" / "mtp_fetch.py", payload / "control" / "tools" / "mtp_fetch.py")

    for archive_name, folder_name in ENGINE_DIRS.items():
        dest = payload / "components" / "engine" / folder_name
        dest.mkdir(parents=True)
        with tarfile.open(fetch(archive_name)) as archive:
            archive.extractall(dest)

    with zipfile.ZipFile(fetch("llama.zip")) as archive:
        archive.extractall(STAGE / "llama")
    tops = [item for item in (STAGE / "llama").iterdir() if item.is_dir()]
    if len(tops) != 1:
        raise SystemExit(f"llama.cpp zip layout: {tops}")
    (payload / "third_party").mkdir(parents=True)
    shutil.move(str(tops[0]), str(payload / "third_party" / "llama.cpp"))

    (payload / "host").mkdir(parents=True)
    copy_tree(ROOT / "apps" / "host" / "server", payload / "host" / "server")
    copy_tree(ROOT / "apps" / "host" / "dist", payload / "host" / "dist")
    if not (payload / "host" / "dist" / "index.html").is_file():
        raise SystemExit("host/dist/index.html is missing; build the UI before packing")

    licenses = payload / "LICENSES"
    licenses.mkdir()
    shutil.copy2(ROOT / "LICENSE", licenses / "Strata-MIT.txt")
    with zipfile.ZipFile(CACHE / "node.zip") as archive:
        (licenses / "node-LICENSE").write_bytes(archive.read("node-v24.12.0-win-x64/LICENSE"))
    for name in ("LICENSE", "LICENSE.txt"):
        src = payload / "control" / "python" / name
        if src.is_file():
            shutil.copy2(src, licenses / f"python-{name}")

    (payload / "Start-Strata.cmd").write_text(textwrap.dedent(r"""
        @echo off
        setlocal
        cd /d "%~dp0"
        "%~dp0control\python\python.exe" "%~dp0control\strata.py" offline --root "%~dp0." %*
        if errorlevel 1 (
          echo.
          echo Strata did not start. The message above names what is missing.
          pause
        )
    """).lstrip(), encoding="utf-8")

    report = preflight(payload)
    if not report.ok:
        for issue in report.issues:
            print(issue.line(), file=sys.stderr)
        raise SystemExit("staged folder failed the offline preflight")
    import binary_deps
    for folder_name in ENGINE_DIRS.values():
        bad = binary_deps.uncovered(payload / "components" / "engine" / folder_name)
        if bad:
            raise SystemExit(f"{folder_name} import closure is open: {bad[:8]}")
    node_bad = binary_deps.uncovered(payload / "node")
    if node_bad:
        raise SystemExit(f"node import closure is open: {node_bad[:8]}")

    readme = textwrap.dedent("""
        Strata 离线安装包（Windows 10/11 x64，仅 NVIDIA）

        这个包同时带有两套官方引擎，目录分开，不会把两套 DLL 放进同一个程序搜索路径：
        - CUDA 12.9：components\\engine\\0.1.55-windows-x86_64-cuda-cuda12
          显卡代码 sm_60、sm_61、sm_70、sm_75、sm_80、sm_86、sm_89（另有 compute_89 的 PTX）
          驱动门槛以引擎清单为准：NVIDIA 576.02 或更新
        - CUDA 13.0：components\\engine\\0.1.55-windows-x86_64-cuda-cuda13
          显卡代码 sm_75、sm_86、sm_89、sm_120（另有 compute_120 的 PTX）
          驱动门槛以引擎清单为准：NVIDIA 580.65 或更新

        启动时用 nvidia-smi 读取驱动版本和显卡计算能力，按上面的清单选择。
        两套都满足时用 CUDA 13。驱动或显卡达不到 CUDA 13 时用 CUDA 12。
        电脑里装了 CUDA Toolkit 13 不会被当成驱动已经达标。
        手动指定：Start-Strata.cmd --cuda 13 或 --cuda 12。指定的那套若不符合清单，程序退出并说明原因，不会偷偷改用另一套。

        没有网也能安装和第一次打开。不安装 WebView2、Python、Node、VC++ 或 CUDA Toolkit。
        用这台电脑上已经有的浏览器打开窗口里打印的 http://127.0.0.1:8791/ 。
        模型权重不在这个包里。在页面里选择已经下好的 GGUF，或 Start-Strata.cmd --model 指向那个文件或文件夹。
        --model 交给页面作为本地目录，不会因此下载权重。
        本包能识别的模型架构是 qwen4exp（Qwen3.8-Flash-Next）。DeepSeek 引擎不在这个包里。
        草稿层和图片编码器如果本地没有，就保持关闭，不会下载。

        显卡驱动是唯一需要目标系统自己具备的 GPU 组件。没有 NVIDIA 驱动时，程序只报告缺驱动并退出。
    """).strip() + "\n"
    (payload / "使用说明.txt").write_text(readme, encoding="utf-8")
    (licenses / "README.txt").write_text(
        "Node's license is node-LICENSE from the pinned nodejs.org zip. "
        "Python's license, if present, is copied from the pinned python-build-standalone tree inside Control. "
        "cuBLAS and the CUDA runtime DLLs are the files shipped inside the upstream Strata engine packages "
        "(Niko1221/Strata v0.2.9); they are not taken from a CUDA toolkit on this build machine. "
        "The Visual C++ runtime DLLs are the ones shipped inside that Control Python tree "
        "(python\\vcruntime140.dll and python\\vcruntime140_1.dll) and, where a wheel ships its own, under python\\Lib. "
        "node.exe in Node 24.12.0 imports only Windows system DLLs.\n",
        encoding="utf-8")

    nsi = STAGE / "offline.nsi"
    outfile = Path("/tmp") / "Strata-0.2.9-offline-windows-x64-cuda12-cuda13-setup.exe"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    nsi.write_text(textwrap.dedent(f"""
        Unicode true
        SetCompressor zlib
        RequestExecutionLevel user
        Name "Strata Offline"
        OutFile "{outfile.as_posix()}"
        InstallDir "$LOCALAPPDATA\\StrataOffline"
        ShowInstDetails show
        Section
          SetOutPath "$INSTDIR"
          File /r "{payload.as_posix()}/*"
          CreateShortcut "$DESKTOP\\Strata Offline.lnk" "$INSTDIR\\Start-Strata.cmd"
        SectionEnd
    """).lstrip(), encoding="utf-8")
    subprocess.run(["makensis", "-NOCD", str(nsi)], check=True)
    digest = sha256(outfile)
    # The sync folder rejects one file around 128MB (close returns EINVAL). Keep the installer on
    # local disk and also write 48MB parts there.
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    part_prefix = OUT_DIR / (outfile.name + ".part-")
    subprocess.run(["split", "-b", "48M", "-d", "-a", "2", str(outfile), str(part_prefix)], check=True)
    (OUT_DIR / f"{outfile.name}.sha256").write_text(
        f"{digest}  {outfile.name}\nsize {outfile.stat().st_size}\n", encoding="utf-8")
    (OUT_DIR / "使用说明.txt").write_text(readme, encoding="utf-8")
    print(f"{outfile} {outfile.stat().st_size} {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
