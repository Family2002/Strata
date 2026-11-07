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
import re
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
    choice_note: str = ""

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
    """The PE closure checker. It lives in packaging/ in a source checkout.

    The offline installer copies that one file to control/packaging/binary_deps.py. This file is
    then control/strata_control/windows_offline.py, so the parent of strata_control is control, and
    control/packaging is the same relative place as <repo>/packaging in a checkout.
    """
    here = Path(__file__).resolve()
    candidates = [here.parents[1] / "packaging", here.parent]
    for folder in candidates:
        if (folder / "binary_deps.py").is_file():
            folder_s = str(folder)
            if folder_s not in sys.path:
                sys.path.insert(0, folder_s)
            import binary_deps
            return binary_deps
    raise ModuleNotFoundError(
        "binary_deps.py is not in the offline install (expected control/packaging/binary_deps.py). "
        "Nothing was downloaded.")


def _reader():
    tools = str(Path(__file__).resolve().parents[1] / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import gguf_reader
    return gguf_reader


# Names a CUDA toolkit directory puts on PATH. The driver DLL (nvcuda.dll) is not one of these.
_CUDA_TOOLKIT_PREFIXES = (
    "cublas64_", "cublaslt64_", "cudart64_", "nvrtc64_", "cufft64_", "curand64_",
    "cusparse64_", "cusolver64_", "nvjitlink64_",
)
_DLL_JOIN = re.compile(
    r"os\.path\.join\(\s*os\.path\.dirname\(\s*__file__\s*\)\s*,(?P<parts>.*?)\)",
    re.S,
)


def _machine_codes(package: dict) -> list[int]:
    found = []
    for arch in package.get("archs") or []:
        kind, _, number = str(arch).partition("_")
        if kind == "sm" and number.isdigit():
            found.append(int(number))
    return found


def directory_has_cuda_toolkit_dll(folder: Path) -> bool:
    """True when this directory itself holds a CUDA toolkit library (not a file in a subdirectory)."""
    try:
        names = [item.name.lower() for item in folder.iterdir() if item.is_file()]
    except OSError:
        return False
    return any(name.startswith(_CUDA_TOOLKIT_PREFIXES) and name.endswith(".dll") for name in names)


def path_without_cuda_toolkit(path: str, *, keep: list[str] | None = None) -> str:
    """Drop PATH entries that contain a CUDA toolkit DLL.

    The chosen engine directory is kept: its cuBLAS is the one the package shipped. A toolkit
    installed on the PC is not. Windows still searches System32 for the driver without PATH.
    """
    keep_resolved: set[str] = set()
    for item in keep or []:
        try:
            keep_resolved.add(str(Path(item).resolve()))
        except OSError:
            keep_resolved.add(str(item))
    kept: list[str] = []
    for entry in str(path or "").split(os.pathsep):
        if not entry:
            continue
        try:
            resolved = str(Path(entry).resolve())
        except OSError:
            resolved = entry
        if resolved in keep_resolved or not directory_has_cuda_toolkit_dll(Path(entry)):
            kept.append(entry)
    return os.pathsep.join(kept)


def _added_dll_directories(folder: Path) -> dict[Path, set[Path]]:
    """Directories a package adds with os.add_dll_directory, keyed by the package directory.

    CPython 3.8+ does not search PATH or an arbitrary subdirectory for an extension's DLLs. It
    searches the extension's own directory, the directory added at startup (python.exe's folder),
    and directories an imported module passed to add_dll_directory. NumPy's delvewheel patch is
    that last kind: numpy/__init__.py adds the sibling numpy.libs folder.
    """
    registered: dict[Path, set[Path]] = {}
    for script in folder.rglob("*.py"):
        try:
            text = script.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "add_dll_directory" not in text:
            continue
        for match in _DLL_JOIN.finditer(text):
            parts = []
            for pardir, double, single in re.findall(r"(os\.pardir)|\"([^\"]+)\"|'([^']+)'", match.group("parts")):
                if pardir:
                    parts.append("..")
                elif double or single:
                    parts.append(double or single)
            if not parts:
                continue
            target = script.parent
            for part in parts:
                target = target.parent if part == ".." else target / part
            if target.is_dir():
                registered.setdefault(script.parent, set()).add(target)
    return registered


def _names_in(folder: Path) -> set[str]:
    try:
        return {item.name.lower() for item in folder.iterdir() if item.is_file()}
    except OSError:
        return set()


def closure_issues(folder: Path, *, python_runtime: bool = False) -> list[Issue]:
    """PE files under `folder` whose imports the loader would not find.

    An engine or Node binary is resolved like a normal Windows exe: its own directory, then
    BUILD.json lib_dirs. PATH is not counted, so a CUDA toolkit on the PC cannot fill a gap.
    A CPython tree also counts directories its packages add with add_dll_directory, and the
    python.exe directory CPython adds at startup. A DLL that merely exists somewhere else in
    the tree does not count.
    """
    if not folder.is_dir():
        return [Issue(str(folder), "missing local directory. Nothing was downloaded.")]
    deps = _deps()
    lib_dirs = {folder}
    build = folder / "BUILD.json"
    if build.is_file():
        try:
            import json
            for rel in json.loads(build.read_text(encoding="utf-8")).get("lib_dirs", []):
                lib_dirs.add(folder / str(rel))
        except (OSError, ValueError):
            pass
    registered = _added_dll_directories(folder) if python_runtime else {}
    app_names = _names_in(folder)
    skip = {".json", ".txt", ".md", ".cu", ".h", ".py", ".yaml", ".yml", ".metallib", ".bin", ".html", ".mjs", ".js", ".css", ".map", ".svg"}
    problems: list[Issue] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.suffix.lower() in skip:
            continue
        try:
            kind, names = deps.imports(path)
        except (OSError, ValueError):
            continue
        if kind != "pe":
            continue
        allowed = set(app_names)
        allowed.update(_names_in(path.parent))
        for lib in lib_dirs:
            allowed.update(_names_in(lib))
        if python_runtime:
            for parent in [path.parent, *path.parents]:
                for added in registered.get(parent, ()):
                    allowed.update(_names_in(added))
                if parent == folder:
                    break
        for name in names:
            if name.lower() in allowed:
                continue
            if deps._match(name, deps.WINDOWS_OS, True) or deps._match(name, deps.WINDOWS_DRIVER, True):
                continue
            problems.append(Issue(
                str(path),
                f"needs {name}, which is not next to it, not in a directory this runtime adds, and not a Windows or graphics-driver library. Nothing was downloaded."))
    return problems


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


def engine_min_driver(root: Path) -> str | None:
    """The NVIDIA driver floor written in the local engine manifest, if it has one."""
    import json
    for pkg in _engine_packages(root):
        try:
            meta = json.loads((pkg / "engine-component.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        floor = (meta.get("min_driver") or {}).get("nvidia")
        if isinstance(floor, str) and floor:
            return floor
    return None


def nvidia_driver_issue(floor: str, have: str | None, error: str | None) -> Issue | None:
    """A missing or too-old NVIDIA driver. `have` is None when nvidia-smi did not answer."""
    if error or not have:
        return Issue("NVIDIA driver",
                     f"the NVIDIA driver was not found ({error or 'nvidia-smi did not answer'}). "
                     f"This package needs driver {floor} or newer. Nothing was installed.")
    def parts(text: str) -> tuple:
        return tuple(int(piece) for piece in text.split(".") if piece.isdigit())
    if parts(have) < parts(floor):
        return Issue("NVIDIA driver",
                     f"driver {have} is older than {floor}. Nothing was installed.")
    return None


def cuda_packages(root: Path) -> list[dict]:
    """Local CUDA engine packages, one dict each. Empty fields mean the manifest could not be read."""
    import json
    found = []
    for folder in _engine_packages(root):
        try:
            meta = json.loads((folder / "engine-component.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            meta = {}
        found.append({
            "folder": folder,
            "name": folder.name,
            "variant": str(meta.get("variant") or ""),
            "min_driver": str((meta.get("min_driver") or {}).get("nvidia") or ""),
            "archs": [str(item) for item in (meta.get("archs") or [])],
        })
    return found


def choose_cuda(packages: list[dict], *, archs: list[str], driver: str | None, manual: str | None) -> tuple[dict | None, str]:
    """Pick one local CUDA package.

    When the card code is known, automatic choice prefers the higher driver floor (CUDA 13 over
    CUDA 12) among packages whose manifest covers that card and this driver. When the card code
    was not reported, a newer package is not guessed: the package that still includes the oldest
    card code is used, and a package that only starts at sm_75 or newer is not chosen by itself.
    A manual choice that fails the driver floor, or the card code when one was reported, is
    refused. Nothing here reads whether a CUDA toolkit is installed.
    """
    from strata_control.engine_component import _version_key, arch_covers

    def fits(package: dict, *, check_arch: bool) -> tuple[bool, str]:
        label = package["variant"] or package["name"]
        if check_arch and archs and package["archs"] and not arch_covers(package["archs"], archs):
            return False, (f"{label} has code for {', '.join(package['archs'])}, this GPU is {', '.join(archs)}")
        floor = package["min_driver"]
        if floor:
            if not driver:
                return False, f"{label} needs NVIDIA driver {floor} or newer, and no driver version was reported"
            if _version_key(driver) < _version_key(floor):
                return False, f"{label} needs NVIDIA driver {floor} or newer, this PC has {driver}"
        return True, ""

    if manual:
        wanted = f"cuda{manual}"
        chosen = [item for item in packages if item["variant"] == wanted]
        if not chosen:
            return None, f"no local {wanted} engine package. Nothing was downloaded."
        ok, why = fits(chosen[0], check_arch=True)
        if not ok:
            return None, why + ". A CUDA toolkit on the PC does not replace the driver. Nothing was installed."
        if not archs:
            return chosen[0], (f"CUDA {manual} was requested. The GPU compute capability was not reported, "
                               "so the card code was not checked. The driver meets this package's floor.")
        return chosen[0], f"CUDA {manual} was requested, and this GPU and driver meet that package's manifest."
    ranked = sorted(packages, key=lambda item: _version_key(item["min_driver"] or "0"), reverse=True)
    usable, reasons = [], []
    for item in ranked:
        ok, why = fits(item, check_arch=bool(archs))
        if ok:
            usable.append(item)
        elif why:
            reasons.append(why)
    if not usable:
        return None, " ".join(reasons) or "no local CUDA engine fits this PC. Nothing was installed."
    if not archs:
        coded = [item for item in usable if _machine_codes(item)]
        if not coded:
            return None, ("the GPU compute capability was not reported, and no local engine lists the card codes it can run. "
                          "Nothing was selected.")
        oldest = min(min(_machine_codes(item)) for item in coded)
        if oldest >= 75:
            return None, ("the GPU compute capability was not reported. Every local package starts at sm_75 or newer, "
                          "so none can be chosen without knowing the card. Pass --cuda 12 or --cuda 13 when the card is known. "
                          "Nothing was selected.")
        wide = [item for item in coded if min(_machine_codes(item)) == oldest]
        wide.sort(key=lambda item: _version_key(item["min_driver"] or "0"), reverse=True)
        picked = wide[0]
        skipped = [item["variant"] or item["name"] for item in usable if item is not picked]
        note = (f"{picked['variant'] or picked['name']} is used because the GPU compute capability was not reported. "
                "The package that still includes the oldest card code is used.")
        if skipped:
            note += " Not selected: " + ", ".join(skipped) + ". Pass --cuda 12 or --cuda 13 to choose one."
        return picked, note
    picked = usable[0]
    other = " ".join(item["variant"] or item["name"] for item in usable[1:])
    note = (f"{picked['variant'] or picked['name']} is used (driver {driver or 'unreported'}, GPU {', '.join(archs)}). "
            "Its folder is the only engine directory put on the library path.")
    if other:
        note += f" Also installed and left unused: {other}."
    return picked, note


def write_engine_pin(components: Path, folder_name: str) -> None:
    """Pin the chosen package the way `engine_component.resolve` already reads a manual pin."""
    import json
    path = components / ".updates" / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    engine = data.get("engine")
    if not isinstance(engine, dict):
        engine = {}
    engine["pinned"] = folder_name
    data["engine"] = engine
    path.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")


def preflight(root: Path, model: Path | None = None, *, port: int = 8791, driver: tuple | None = None,
              archs: list[str] | None = None, manual: str | None = None) -> Report:
    """Check the folder and, when given, the model. Does not start a process and does not use the network.

    `driver` is (have, error) from nvidia-smi. None means this machine is not the target PC, so the
    driver is not judged here. On Windows the installed command checks it.
    """
    root = Path(root)
    report = Report(root=root, model=model if model is not None else root, url=f"http://127.0.0.1:{port}/")
    for rel in RUNTIME_FILES:
        if not (root / rel).exists():
            report.issues.append(Issue(str(root / rel), "missing local file. Nothing was downloaded."))
    package_dirs = _engine_packages(root)
    model_archs: set[str] = set()
    if model is not None:
        files = gguf_files(model)
        if not files:
            report.issues.append(Issue(str(model), "no local .gguf file. Nothing was downloaded."))
        else:
            for item in files:
                issue, arch = inspect_gguf(item)
                if issue is not None:
                    report.issues.append(issue)
                    continue
                if arch:
                    model_archs.add(arch)
    if (not model_archs or "qwen4exp" in model_archs) and not package_dirs:
        report.issues.append(Issue(
            str(root / "components" / "engine" / "<package>" / "engine-component.json"),
            "missing local engine package. Nothing was downloaded."))
    if "deepseek41" in model_archs and not _dsv41_exes(root):
        report.issues.append(Issue(
            str(root / "components" / "engine-dsv41" / "<package>" / "strata-dsv41.exe"),
            "missing local DeepSeek engine. This CUDA package does not contain it. Nothing was downloaded."))
    if driver is not None or manual:
        have, error = driver if driver is not None else (None, None)
        facts = cuda_packages(root)
        if driver is not None and not have:
            floors = ", ".join(f"{item['variant']} needs {item['min_driver']}" for item in facts if item["min_driver"]) or "an NVIDIA driver"
            report.issues.append(Issue("NVIDIA driver",
                                       f"the NVIDIA driver was not found ({error or 'nvidia-smi did not answer'}). {floors}. "
                                       "Installing the CUDA toolkit does not provide this. Nothing was installed."))
        else:
            chosen, note = choose_cuda(facts, archs=list(archs or []), driver=have, manual=manual)
            if chosen is None:
                report.issues.append(Issue("CUDA engine", note))
            else:
                report.choice_note = note
                report.choice = chosen  # type: ignore[attr-defined]
    for folder in (root / "node", *package_dirs, *[exe.parent for exe in _dsv41_exes(root)]):
        if folder.is_dir():
            report.issues.extend(closure_issues(folder))
    python_runtime = root / "control" / "python"
    if python_runtime.is_dir():
        report.issues.extend(closure_issues(python_runtime, python_runtime=True))
    report.argv, report.env = host_process(root, port, model)
    return report


def model_directory(model: Path) -> str | None:
    """The folder the host should prepare. A file path becomes its parent; a missing path is not invented."""
    path = Path(model)
    if path.is_file():
        path = path.parent
    if not path.is_dir():
        return None
    return str(path.resolve())


def host_process(root: Path, port: int, model: Path | None = None) -> tuple[list[str], dict[str, str]]:
    """The host command and the environment that keeps it from acquiring anything.

    A model path is handed to the host as STRATA_OFFLINE_MODEL (the folder). The page's install
    uses that folder as gguf_dir, so the first preparation does not try to download weights.
    """
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
    if model is not None:
        folder = model_directory(model)
        if folder:
            env["STRATA_OFFLINE_MODEL"] = folder
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
        if report.model != report.root:
            lines.append(f"  {report.model}")
        for item in cuda_packages(report.root):
            floor = item["min_driver"] or "not stated"
            arch = ", ".join(item["archs"]) or "not stated"
            lines.append(f"  {item['name']}: variant {item['variant'] or '?'}, NVIDIA driver {floor} or newer, code {arch}")
        if report.choice_note:
            lines.append(report.choice_note)
    else:
        lines.append("Missing or unusable local files:")
        lines.extend(f"  {issue.line()}" for issue in report.issues)
    lines.append("")
    lines.append("This PC must already have:")
    lines.extend(f"  {item}" for item in HOST_PREREQUISITES)
    return "\n".join(lines)


def read_nvidia_driver() -> tuple[str | None, str | None]:
    """(version, error) from nvidia-smi. Used on the target PC only."""
    import subprocess
    try:
        done = subprocess.run(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                              capture_output=True, text=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    if done.returncode != 0:
        return None, (done.stderr or done.stdout or f"exit {done.returncode}").strip()
    line = next((item.strip() for item in done.stdout.splitlines() if item.strip()), "")
    return (line or None), (None if line else "nvidia-smi printed no driver version")


def begin(root: Path, model: Path | None = None, port: int = 8791, *, plan: bool = False, spawn=None,
          check_driver: bool | None = None, driver_info: tuple | None = None, archs: list[str] | None = None,
          manual: str | None = None):
    """Preflight, then start the host unless `plan` is set. `spawn` is injectable for tests."""
    if check_driver is None:
        check_driver = os.name == "nt"
    if check_driver and driver_info is None:
        driver_info = read_nvidia_driver()
    if check_driver and archs is None:
        from strata_control.engine_component import cuda_archs_present
        archs = cuda_archs_present()
    report = preflight(root, model, port=port, driver=driver_info if check_driver or manual else None, archs=archs, manual=manual)
    if not report.ok:
        raise OfflineStop(report.issues)
    chosen = getattr(report, "choice", None)
    if chosen is not None:
        from strata_control.engine_component import package_lib_dirs
        dirs = package_lib_dirs(chosen["folder"])
        # The chosen package stays. A CUDA toolkit, or the other engine, is removed from PATH so a
        # later LoadLibrary cannot bind the PC's cublas instead of the one in the package.
        tail = path_without_cuda_toolkit(report.env.get("PATH", ""), keep=dirs)
        report.env["PATH"] = os.pathsep.join([*dirs, tail]).strip(os.pathsep)
        variant = chosen["variant"]
        report.env["STRATA_OFFLINE_CUDA"] = "13" if variant.endswith("13") else "12" if variant.endswith("12") else variant
        if not plan:
            write_engine_pin(Path(root) / "components", chosen["name"])
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
    ap.add_argument("--model", default=None, help="local .gguf file or folder; the host prepares that folder and does not download weights")
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--cuda", choices=("12", "13"), default=None, help="use this CUDA engine only when its manifest allows the GPU and driver")
    ap.add_argument("--plan", action="store_true", help="check the folder and print the address; do not start")
    args = ap.parse_args(argv)
    model = Path(args.model) if args.model else None
    try:
        report = begin(Path(args.root), model, args.port, plan=True, manual=args.cuda)
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
    started = begin(Path(args.root), model, args.port, plan=False, manual=args.cuda)
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
