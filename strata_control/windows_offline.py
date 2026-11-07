"""Windows offline entry: a local page, a bundled runtime, no installer and no download.

The desktop installer asks Windows for Microsoft WebView2 and will download that installer when
the PC does not already have it. This entry does not start that shell. It starts the same App Host
with the Node binary that was packed beside it, and prints http://127.0.0.1:<port>/ for a browser
the PC already has.

It is opt-in (`strata offline`). `strata launch`, the desktop app and `strata update` keep their
own behaviour.

What the preparation environment puts in the folder is checked here, including the PE import
closure of Node, the bundled Python and the engine. A missing file is named and the process
exits. Nothing in this module opens a socket, runs winget, pip, git or cmake, or starts an
installer.

Host prerequisites this module does not carry and does not install: Windows 10/11 x64, the
graphics driver for the engine package (nvcuda.dll or the AMD driver), and the Windows libraries
listed in packaging/binary_deps.py WINDOWS_OS (kernel32.dll, ucrtbase.dll, and the rest of that
tuple). WebView2, a separate Python, a separate Node, the Visual C++ redistributable and the CUDA
toolkit are not prerequisites: their DLLs have to be in the folder, or the closure check fails.
"""
from __future__ import annotations

import os
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

ENV = "STRATA_OFFLINE_ENTRY"

# Architectures this entry will point at local tools for. Anything else is refused here, before a
# download or a compiler could be offered. qwen4exp is Qwen3.8-Flash-Next; deepseek41 is
# DeepSeek-V4.1-Flash. Helper GGUFs (a picture reader, a PLE table) are not models.
MODEL_ARCH = {
    "qwen4exp": "strata.exe",
    "deepseek41": "strata-dsv41.exe",
}
HELPER_ARCH = {"strata-ple", "strata-embd", "engram", "deepseek41-engram", "controlvector"}

# Files the folder must contain. The llama.cpp tree is the Python preparation tools (gguf-py).
# ggml/CMakeLists.txt is the marker get_llama_cpp already uses; this entry never runs cmake.
RUNTIME_FILES = (
    "node/node.exe",
    "host/server/index.mjs",
    "host/dist/index.html",
    "control/python/python.exe",
    "control/strata.py",
    "control/strata_control/__init__.py",
    "third_party/llama.cpp/gguf-py/gguf/__init__.py",
    "third_party/llama.cpp/ggml/CMakeLists.txt",
)

HOST_PREREQUISITES = (
    "Windows 10 or Windows 11, x64",
    "the graphics driver for the engine package already in this folder (NVIDIA or AMD). "
    "Strata does not install it",
    "Windows' own libraries (kernel32.dll, ucrtbase.dll and the rest of WINDOWS_OS in "
    "packaging/binary_deps.py). The Visual C++ runtime is not one of them: it has to sit next to "
    "node.exe and python.exe",
)


def active(environ: dict | None = None) -> bool:
    env = os.environ if environ is None else environ
    return str(env.get(ENV, "")).strip() == "1"


@dataclass
class Issue:
    path: str
    reason: str

    def line(self) -> str:
        return f"{self.path}: {self.reason}"


@dataclass
class Report:
    root: Path
    model: Path
    issues: list[Issue] = field(default_factory=list)
    url: str = ""
    argv: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    proc: object | None = None

    @property
    def ok(self) -> bool:
        return not self.issues


class OfflineStop(Exception):
    """The folder is not complete. exit_code 5 matches Strata's other 'not started' stops."""

    exit_code = 5

    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("\n".join(issue.line() for issue in issues))


def _deps():
    root = Path(__file__).resolve().parents[1]
    packaging = str(root / "packaging")
    if packaging not in sys.path:
        sys.path.insert(0, packaging)
    import binary_deps
    return binary_deps


def _reader():
    tools = str(Path(__file__).resolve().parents[1] / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import gguf_reader
    return gguf_reader


def closure_issues(folder: Path) -> list[Issue]:
    """PE files under `folder` whose imports are neither in the folder nor Windows nor the driver."""
    if not folder.is_dir():
        return [Issue(str(folder), "missing local directory. Nothing was downloaded.")]
    bad = _deps().uncovered(folder)
    return [Issue(str(folder / rel), f"needs {name}, which is not in this folder and is not a Windows or graphics-driver library. Nothing was downloaded.")
            for rel, name in bad]


def _engine_packages(root: Path) -> list[Path]:
    base = root / "components" / "engine"
    if not base.is_dir():
        return []
    return sorted(path.parent for path in base.glob("*/engine-component.json") if path.is_file())


def _dsv41_exes(root: Path) -> list[Path]:
    base = root / "components" / "engine-dsv41"
    if not base.is_dir():
        return []
    return sorted(path for path in base.glob("*/strata-dsv41.exe") if path.is_file())


def gguf_files(path: Path) -> list[Path]:
    """GGUF files at `path`. A picture reader (mmproj) is left out of a directory; a path that is only that file is returned so the caller can refuse it."""
    path = Path(path)
    if path.is_file():
        return [path]
    if not path.is_dir():
        return []
    files = [item for item in sorted(path.glob("*.gguf")) if not item.name.lower().startswith("mmproj")]
    return files


def inspect_gguf(path: Path) -> tuple[Issue | None, str | None]:
    """(issue, architecture). issue is None when this GGUF is a model the offline entry can use.

    A file that is not GGUF, not version 3, or some other architecture is refused. No conversion
    and no download is attempted.
    """
    path = Path(path)
    if not path.is_file():
        return Issue(str(path), "missing local file. Nothing was downloaded."), None
    if path.name.lower().startswith("mmproj"):
        return Issue(str(path), "this is a picture reader (mmproj), not a model. Choose the model's own .gguf file. Nothing was downloaded."), None
    try:
        parsed = _reader().GGUFFile(path)
    except ValueError as exc:
        return Issue(str(path), f"{exc}. This offline entry does not convert other formats and nothing was downloaded."), None
    arch = parsed.metadata.get("general.architecture")
    if not isinstance(arch, str) or not arch:
        return Issue(str(path), "GGUF has no general.architecture. Nothing was downloaded."), None
    if arch in HELPER_ARCH:
        return Issue(str(path), f"GGUF architecture {arch!r} is a helper table, not a model this entry runs. Nothing was downloaded."), arch
    if arch not in MODEL_ARCH:
        known = ", ".join(sorted(MODEL_ARCH))
        return Issue(str(path), f"GGUF architecture {arch!r} is not handled by this offline entry (it handles {known}). Nothing was downloaded."), arch
    return None, arch


def preflight(root: Path, model: Path, *, port: int = 8791) -> Report:
    """Check the folder and the model. Does not start a process and does not use the network."""
    root = Path(root)
    model = Path(model)
    report = Report(root=root, model=model, url=f"http://127.0.0.1:{port}/")
    for rel in RUNTIME_FILES:
        if not (root / rel).exists():
            report.issues.append(Issue(str(root / rel), "missing local file. Nothing was downloaded."))
    packages = _engine_packages(root)
    files = gguf_files(model)
    if not files:
        report.issues.append(Issue(str(model), "no local .gguf file. Nothing was downloaded."))
        archs: set[str] = set()
    else:
        archs = set()
        for item in files:
            issue, arch = inspect_gguf(item)
            if issue is not None:
                report.issues.append(issue)
                continue
            if arch:
                archs.add(arch)
    if "qwen4exp" in archs and not packages:
        report.issues.append(Issue(
            str(root / "components" / "engine" / "<package>" / "engine-component.json"),
            "missing local engine package. Nothing was downloaded."))
    if "deepseek41" in archs and not _dsv41_exes(root):
        report.issues.append(Issue(
            str(root / "components" / "engine-dsv41" / "<package>" / "strata-dsv41.exe"),
            "missing local DeepSeek engine. Nothing was downloaded."))
    for folder in (root / "node", root / "control" / "python", *[pkg for pkg in packages], *[exe.parent for exe in _dsv41_exes(root)]):
        if folder.is_dir():
            report.issues.extend(closure_issues(folder))
    report.argv, report.env = host_process(root, port)
    return report


def host_process(root: Path, port: int) -> tuple[list[str], dict[str, str]]:
    """The host command and the environment that keeps it from acquiring anything."""
    from strata_control.offline import child_environment
    root = Path(root)
    argv = [str(root / "node" / "node.exe"), str(root / "host" / "server" / "index.mjs"), "--dist", str(root / "host" / "dist")]
    env = child_environment(offline=True)
    env.update({
        ENV: "1",
        "STRATA_NO_ACQUIRE": "1",
        "STRATA_CONTROL_INSTALL": "1",
        "STRATA_UI_MODE": "browser",
        "STRATA_NO_BUNDLED_ENGINE": "1",
        "STRATA_BIND": "127.0.0.1",
        "STRATA_PORT": str(port),
        "STRATA_DIST": str(root / "host" / "dist"),
        "STRATA_CONTROL_DIR": str(root / "control"),
        "STRATA_COMPONENTS_DIR": str(root / "components"),
        "STRATA_INSTALL_DIR": str(root),
        "PYTHONNOUSERSITE": "1",
        "PYTHONUTF8": "1",
    })
    return argv, env


def format_report(report: Report) -> str:
    lines = [f"Local page: {report.url}", "Open that address in a browser already on this PC. This program does not install one.", ""]
    if report.ok:
        lines.append("Local files this start uses:")
        for rel in RUNTIME_FILES:
            lines.append(f"  {report.root / rel}")
        for pkg in _engine_packages(report.root):
            lines.append(f"  {pkg / 'engine-component.json'}")
        for exe in _dsv41_exes(report.root):
            lines.append(f"  {exe}")
        lines.append(f"  {report.model}")
    else:
        lines.append("Missing or unusable local files:")
        lines.extend(f"  {issue.line()}" for issue in report.issues)
    lines.append("")
    lines.append("This PC must already have:")
    lines.extend(f"  {item}" for item in HOST_PREREQUISITES)
    return "\n".join(lines)


def begin(root: Path, model: Path, port: int = 8791, *, plan: bool = False, spawn=None):
    """Preflight, then start the host unless `plan` is set. `spawn` is injectable for tests."""
    report = preflight(root, model, port=port)
    if not report.ok:
        raise OfflineStop(report.issues)
    if plan:
        return report
    if spawn is None:
        import subprocess
        spawn = subprocess.Popen
    report.proc = spawn(report.argv, env=report.env, cwd=str(Path(root) / "host"))
    return report


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="strata offline", description="Start the local page from a prepared Windows folder. Downloads nothing and installs nothing.")
    ap.add_argument("--root", required=True, help="the prepared folder (node, host, control, components, third_party)")
    ap.add_argument("--model", required=True, help="a local .gguf file or a folder of them")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--plan", action="store_true", help="check the folder and print the address; do not start")
    args = ap.parse_args(argv)
    try:
        report = begin(Path(args.root), Path(args.model), args.port, plan=True)
    except OfflineStop as exc:
        print(exc, file=sys.stderr)
        print("Nothing was downloaded and no installer was started.", file=sys.stderr)
        return exc.exit_code
    print(format_report(report))
    if args.plan:
        return 0
    if os.name != "nt":
        print("This offline entry runs on Windows. It was not started on this system. Nothing was downloaded.", file=sys.stderr)
        return 5
    started = begin(Path(args.root), Path(args.model), args.port, plan=False)
    proc = started.proc
    try:
        return int(proc.wait())
    except KeyboardInterrupt:
        proc.terminate()
        return 130


# A tiny GGUF writer used by nothing else. Kept here so a caller can see the header shape the
# inspector accepts, and so tests do not need numpy. Not a model converter.
def write_header(path: Path, architecture: str) -> None:
    """Write a GGUF v3 file with no tensors and one metadata key. For tests and local probes."""
    def blob(text: str) -> bytes:
        raw = text.encode()
        return struct.pack("<Q", len(raw)) + raw
    body = struct.pack("<I", 0x46554747) + struct.pack("<IQQ", 3, 0, 1)
    body += blob("general.architecture") + struct.pack("<I", 8) + blob(architecture)
    Path(path).write_bytes(body)
