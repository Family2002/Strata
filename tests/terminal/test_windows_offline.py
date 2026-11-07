"""The Windows offline entry: a prepared folder, no download, no installer.

These tests do not start a GPU engine and do not claim a Windows run. They build a fake Windows
folder, including paths with spaces and Chinese characters, and refuse any socket the code opens.
"""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packaging"))

import setup  # noqa: E402
from strata_cli.launch.runner import LaunchError, Runner  # noqa: E402
from strata_cli.launch.settings import LaunchSettings  # noqa: E402
from strata_control import windows_offline as wo  # noqa: E402
from strata_control.updater import UpdateError, _download  # noqa: E402
import node_runtime  # noqa: E402


def fake_pe(imports, delay=()):
    """A minimal PE32+ image. Same layout as tools/test_dll_closure.py."""
    text = bytearray()
    va = 0x1000

    def add_str(s):
        off = len(text)
        text.extend(s.encode() + b"\0")
        return va + off

    names = [add_str(n) for n in imports]
    dnames = [add_str(n) for n in delay]
    while len(text) % 8:
        text.append(0)
    imp_off = len(text)
    for rva in names:
        text.extend(struct.pack("<IIIII", 0, 0, 0, rva, 0))
    text.extend(b"\0" * 20)
    dly_off = len(text)
    for rva in dnames:
        text.extend(struct.pack("<IIIIIIII", 1, rva, 0, 0, 0, 0, 0, 0))
    text.extend(b"\0" * 32)
    opt_size = 112 + 16 * 8
    pe = 0x80
    hdr = bytearray(0x200)
    hdr[0:2] = b"MZ"
    struct.pack_into("<I", hdr, 0x3C, pe)
    hdr[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", hdr, pe + 4, 0x8664, 1, 0, 0, 0, opt_size, 0x2022)
    opt = pe + 24
    struct.pack_into("<H", hdr, opt, 0x20B)
    dd = opt + 112
    struct.pack_into("<II", hdr, dd + 8 * 1, va + imp_off, 20 * (len(names) + 1))
    struct.pack_into("<II", hdr, dd + 8 * 13, va + dly_off, 32 * (len(dnames) + 1))
    sec = opt + opt_size
    hdr[sec:sec + 8] = b".idata\0\0"
    struct.pack_into("<IIII", hdr, sec + 8, len(text), va, len(text), 0x200)
    return bytes(hdr) + bytes(text)


def _tree(root: Path, *, vcruntime: bool = True, engine: bool = True, llama: bool = True, dsv41: bool = False) -> None:
    node = root / "node"
    node.mkdir(parents=True, exist_ok=True)
    (node / "node.exe").write_bytes(fake_pe(["KERNEL32.dll", "VCRUNTIME140.dll"]))
    if vcruntime:
        (node / "vcruntime140.dll").write_bytes(fake_pe(["KERNEL32.dll"]))
    py = root / "control" / "python"
    py.mkdir(parents=True, exist_ok=True)
    (py / "python.exe").write_bytes(fake_pe(["KERNEL32.dll", "VCRUNTIME140.dll", "python312.dll"]))
    if vcruntime:
        (py / "vcruntime140.dll").write_bytes(fake_pe(["KERNEL32.dll"]))
        (py / "python312.dll").write_bytes(fake_pe(["KERNEL32.dll", "VCRUNTIME140.dll"]))
    (root / "control" / "strata.py").write_text("print('offline')\n", encoding="utf-8")
    pkg = root / "control" / "strata_control"
    pkg.mkdir(exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (root / "host" / "server").mkdir(parents=True, exist_ok=True)
    (root / "host" / "server" / "index.mjs").write_text("process.exit(0)\n", encoding="utf-8")
    (root / "host" / "dist").mkdir(exist_ok=True)
    (root / "host" / "dist" / "index.html").write_text("<p>local</p>\n", encoding="utf-8")
    if llama:
        llama_dir = root / "third_party" / "llama.cpp"
        (llama_dir / "gguf-py" / "gguf").mkdir(parents=True, exist_ok=True)
        (llama_dir / "gguf-py" / "gguf" / "__init__.py").write_text("", encoding="utf-8")
        (llama_dir / "ggml").mkdir(exist_ok=True)
        (llama_dir / "ggml" / "CMakeLists.txt").write_text("# marker only\n", encoding="utf-8")
    if engine:
        eng = root / "components" / "engine" / "0.1.55-windows-x86_64-cuda-cuda13"
        eng.mkdir(parents=True, exist_ok=True)
        (eng / "engine-component.json").write_text("{}\n", encoding="utf-8")
        (eng / "strata.exe").write_bytes(fake_pe(["KERNEL32.dll", "cublas64_13.dll", "VCRUNTIME140.dll"]))
        (eng / "cublas64_13.dll").write_bytes(fake_pe(["KERNEL32.dll", "VCRUNTIME140.dll"]))
        (eng / "vcruntime140.dll").write_bytes(fake_pe(["KERNEL32.dll"]))
    if dsv41:
        deep = root / "components" / "engine-dsv41" / "0.1.55-windows-x86_64-cuda-cuda13"
        deep.mkdir(parents=True, exist_ok=True)
        (deep / "strata-dsv41.exe").write_bytes(fake_pe(["KERNEL32.dll", "cublas64_13.dll"]))
        (deep / "cublas64_13.dll").write_bytes(fake_pe(["KERNEL32.dll"]))


def _bomb(*_a, **_k):
    raise AssertionError("network or external tool")


class OfflineEntry(unittest.TestCase):
    def test_missing_engine_and_llama_do_not_touch_the_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "模型 目录"
            model = Path(tmp) / "我的模型" / "Qwen 本地.gguf"
            model.parent.mkdir()
            wo.write_header(model, "qwen4exp")
            _tree(root, engine=False, llama=False)
            with mock.patch("urllib.request.urlopen", _bomb), mock.patch("urllib.request.urlretrieve", _bomb):
                report = wo.preflight(root, model)
            text = "\n".join(issue.line() for issue in report.issues)
            self.assertIn("engine-component.json", text)
            self.assertIn("gguf-py", text)
            self.assertNotIn("winget", text.lower())
            self.assertNotIn("webview2", text.lower())
            self.assertNotIn("https://", text)
            with self.assertRaises(wo.OfflineStop):
                wo.begin(root, model, spawn=_bomb, check_driver=False)

    def test_complete_folder_starts_the_host_on_a_path_with_spaces_and_chinese(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "离线 入口"
            model = root / "模型 文件" / "flash next.gguf"
            model.parent.mkdir(parents=True)
            wo.write_header(model, "qwen4exp")
            _tree(root)
            seen = []

            def spawn(argv, env=None, cwd=None):
                seen.append((argv, env, cwd))
                return mock.Mock()

            with mock.patch("urllib.request.urlopen", _bomb), mock.patch("urllib.request.urlretrieve", _bomb):
                report = wo.begin(root, model, port=8791, spawn=spawn, check_driver=False)
            self.assertEqual(len(seen), 1)
            argv, env, cwd = seen[0]
            self.assertTrue(argv[0].endswith("node.exe"), argv[0])
            self.assertIn("index.mjs", argv[1])
            self.assertEqual(env["STRATA_OFFLINE_ENTRY"], "1")
            self.assertEqual(env["STRATA_NO_ACQUIRE"], "1")
            self.assertEqual(env["HF_HUB_OFFLINE"], "1")
            self.assertEqual(env["PIP_NO_INDEX"], "1")
            self.assertEqual(env["STRATA_BIND"], "127.0.0.1")
            self.assertEqual(env["STRATA_OFFLINE_MODEL"], str(model.parent.resolve()))
            self.assertEqual(report.url, "http://127.0.0.1:8791/")
            self.assertIn("离线 入口", str(cwd))
            for banned in ("winget", "pip", "git", "cmake"):
                self.assertNotIn(banned, " ".join(argv))

    def test_node_without_its_runtime_dll_is_named(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "folder with spaces"
            model = root / "m.gguf"
            root.mkdir()
            wo.write_header(model, "qwen4exp")
            _tree(root, vcruntime=False)
            report = wo.preflight(root, model)
            text = "\n".join(issue.line() for issue in report.issues)
            self.assertIn("VCRUNTIME140.dll", text)
            self.assertIn("node.exe", text.replace("\\", "/").lower() + text)
            self.assertFalse(report.ok)

    def test_unsupported_and_non_gguf_files_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _tree(root)
            other = root / "weights.safetensors"
            other.write_bytes(b"not a gguf")
            bad = wo.preflight(root, other)
            self.assertTrue(any("not a GGUF" in issue.reason for issue in bad.issues))
            unknown = root / "llama.gguf"
            wo.write_header(unknown, "llama")
            refused = wo.preflight(root, unknown)
            self.assertTrue(any("llama" in issue.reason and "not handled" in issue.reason for issue in refused.issues))
            helper = root / "ple.gguf"
            wo.write_header(helper, "strata-ple")
            helper_report = wo.preflight(root, helper)
            self.assertTrue(any("helper table" in issue.reason for issue in helper_report.issues))

    def test_deepseek_without_its_engine_is_refused_and_with_it_starts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ds"
            model = root / "ds.gguf"
            root.mkdir()
            wo.write_header(model, "deepseek41")
            _tree(root, engine=False, dsv41=False)
            missing = wo.preflight(root, model)
            self.assertTrue(any("strata-dsv41.exe" in issue.path for issue in missing.issues))
            _tree(root, engine=False, dsv41=True)
            seen = []
            with mock.patch("urllib.request.urlopen", _bomb):
                wo.begin(root, model, spawn=lambda argv, env=None, cwd=None: seen.append(argv) or mock.Mock(), check_driver=False)
            self.assertEqual(len(seen), 1)

    def test_node_zip_closure_extracts_the_runtime_and_refuses_a_hole(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            good = tmp / "node.zip"
            with zipfile.ZipFile(good, "w") as zf:
                zf.writestr("node-v24.12.0-win-x64/node.exe", fake_pe(["KERNEL32.dll", "VCRUNTIME140.dll"]))
                zf.writestr("node-v24.12.0-win-x64/vcruntime140.dll", fake_pe(["KERNEL32.dll"]))
            dest = tmp / "node"
            written = node_runtime.extract(good, dest)
            self.assertEqual(sorted(written), ["node.exe", "vcruntime140.dll"])
            hole = tmp / "hole.zip"
            with zipfile.ZipFile(hole, "w") as zf:
                zf.writestr("node-v24.12.0-win-x64/node.exe", fake_pe(["KERNEL32.dll", "VCRUNTIME140.dll"]))
            empty = tmp / "empty"
            with self.assertRaises(node_runtime.NodeRuntimeError) as caught:
                node_runtime.extract(hole, empty)
            self.assertIn("vcruntime140.dll", str(caught.exception).lower())
            self.assertFalse(empty.exists())

    def test_an_installed_control_layout_can_preflight_without_the_repo(self):
        """The real failure: Start-Strata.cmd loads control/strata_control/windows_offline.py, whose
        parent is control, not the source checkout. binary_deps must be at control/packaging."""
        with tempfile.TemporaryDirectory() as tmp:
            install = Path(tmp) / "StrataOffline"
            control = install / "control"
            control.mkdir(parents=True)
            shutil.copytree(ROOT / "strata_control", control / "strata_control",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            packaged = control / "packaging"
            packaged.mkdir()
            shutil.copy2(ROOT / "packaging" / "binary_deps.py", packaged / "binary_deps.py")
            (control / "tools").mkdir()
            shutil.copy2(ROOT / "tools" / "gguf_reader.py", control / "tools" / "gguf_reader.py")
            model = install / "模型" / "q.gguf"
            model.parent.mkdir()
            wo.write_header(model, "qwen4exp")
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            script = (
                "import sys\n"
                "from pathlib import Path\n"
                "install = Path(sys.argv[1])\n"
                "repo = Path(sys.argv[2]).resolve()\n"
                "sys.path = [p for p in sys.path if p and Path(p).resolve() != repo and repo not in Path(p).resolve().parents]\n"
                "sys.path.insert(0, str(install / 'control'))\n"
                "import strata_control.windows_offline as wo\n"
                "report = wo.preflight(install, Path(sys.argv[3]))\n"
                "text = '\\n'.join(issue.line() for issue in report.issues)\n"
                "if 'binary_deps' in text or 'No module named' in text:\n"
                "    raise SystemExit(text)\n"
                "print('preflight-import-ok')\n"
            )
            done = subprocess.run([sys.executable, "-c", script, str(install), str(ROOT), str(model)],
                                  cwd=tmp, env=env, capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr + done.stdout)
            self.assertIn("preflight-import-ok", done.stdout)

    def test_the_installer_script_overlays_the_engine_server(self):
        text = (ROOT / "packaging" / "build_windows_offline_installer.py").read_text(encoding="utf-8")
        self.assertIn('copy_tree(ROOT / "serve", payload / "control" / "serve")', text)
        self.assertIn('Path("/opt/cursor/artifacts/offline-v0.2.11-r2")', text)
        self.assertIn('payload / "control" / "packaging"', text)
        self.assertIn("strata-control-0.1.57-windows-x86_64.zip", text)
        self.assertIn("0.1.57-windows-x86_64-cuda-cuda12", text)

    def test_fetch_scripts_use_the_closure_helper(self):
        sh = (ROOT / "apps/desktop/scripts/fetch-node.sh").read_text(encoding="utf-8")
        ps1 = (ROOT / "apps/desktop/scripts/fetch-node.ps1").read_text(encoding="utf-8")
        self.assertIn("packaging/node_runtime.py", sh)
        self.assertNotIn("unzip -q -j", sh)
        self.assertIn("node_runtime.py", ps1)
        self.assertNotIn("ExtractToFile", ps1)

    def test_setup_download_llama_and_compiler_do_not_leave_the_machine(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = {"STRATA_OFFLINE_ENTRY": "1", "STRATA_INSTALL_DIR": tmp}
            with mock.patch.dict(os.environ, env, clear=False), mock.patch.object(setup, "_urlopen", _bomb):
                with self.assertRaises(SystemExit):
                    setup.get_llama_cpp()
                dest = Path(tmp) / "missing.bin"
                with self.assertRaises(SystemExit):
                    setup.download("https://example.invalid/model.gguf", dest, "model")
                self.assertFalse(dest.exists())
                src = Path(tmp) / "local.gguf"
                src.write_bytes(b"gguf-bytes")
                copied = Path(tmp) / "copied.gguf"
                setup.download(str(src), copied, "local copy")
                self.assertEqual(copied.read_bytes(), b"gguf-bytes")
            with mock.patch.dict(os.environ, {"STRATA_OFFLINE_ENTRY": "1"}, clear=False), \
                    mock.patch.object(setup, "run", _bomb):
                with self.assertRaises(SystemExit):
                    setup.install_build_tools({"arch": 89, "archs": [89]}, True)

    def test_launch_and_update_do_not_fetch_an_engine(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"STRATA_OFFLINE_ENTRY": "1"}, clear=False), \
                    mock.patch("urllib.request.urlopen", _bomb):
                runner = Runner(LaunchSettings(), components_dir=tmp)
                with self.assertRaises(LaunchError) as caught:
                    runner.ensure_engine()
            self.assertIn("does not download", str(caught.exception))
            with mock.patch.dict(os.environ, {"STRATA_OFFLINE_ENTRY": "1"}, clear=False):
                with self.assertRaises(UpdateError) as download:
                    _download(None, "engine", "https://example.invalid/e.tar.gz", 1, "a" * 64, "the engine", None)
                from strata_control.transfer import TransferError, download as transfer_download
                with self.assertRaises(TransferError):
                    transfer_download("https://example.invalid/a.bin", Path(tmp) / "a.part", size=1, sha256="ab" * 32)
                import importlib.util
                spec = importlib.util.spec_from_file_location("mtp_fetch", ROOT / "tools" / "mtp_fetch.py")
                mtp = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mtp)
                with mock.patch.object(sys, "argv", ["mtp_fetch.py", "fetch", "--out", tmp]):
                    with self.assertRaises(SystemExit) as mtp_exit:
                        mtp.main()
                self.assertIn("does not download", str(mtp_exit.exception.code))
                with self.assertRaises(SystemExit):
                    setup.fetch_mtp(Path(tmp) / "mtp", {})
            self.assertIn("does not download", str(download.exception))

    def test_install_job_keeps_the_offline_limit_after_it_clears_acquire(self):
        """The real install job (tools/strata_mcp.py) is what setup runs under. It drops STRATA_NO_ACQUIRE
        and keeps the offline flag and the PATH the host was given. setup._urlopen must still refuse https."""
        import importlib.util
        import json
        loaded = importlib.util.spec_from_file_location("strata_mcp_offline_job", ROOT / "tools" / "strata_mcp.py")
        mcp = importlib.util.module_from_spec(loaded)
        loaded.loader.exec_module(mcp)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "src"
            src.mkdir()
            chosen = root / "engine-cuda13"
            other = root / "engine-cuda12"
            chosen.mkdir()
            other.mkdir()
            out = root / "child.json"
            (src / "setup.py").write_text(
                "import importlib.util, json, os, sys\n"
                "from pathlib import Path\n"
                f"sys.path.insert(0, {str(ROOT)!r})\n"
                f"spec = importlib.util.spec_from_file_location('strata_setup_real', {str(ROOT / 'setup.py')!r})\n"
                "setup = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(setup)\n"
                "import urllib.request\n"
                "info = {\n"
                "    'offline': os.environ.get('STRATA_OFFLINE_ENTRY'),\n"
                "    'acquire': os.environ.get('STRATA_NO_ACQUIRE'),\n"
                "    'path0': os.environ.get('PATH', '').split(os.pathsep)[0],\n"
                "    'path': os.environ.get('PATH', ''),\n"
                "}\n"
                "def boom(*_a, **_k):\n"
                "    info['opened'] = True\n"
                "    raise AssertionError('urlopen')\n"
                "urllib.request.urlopen = boom\n"
                "try:\n"
                "    setup._urlopen(urllib.request.Request('https://example.invalid/model.gguf'))\n"
                "except SystemExit as exc:\n"
                "    info['exit'] = exc.code\n"
                f"Path({str(out)!r}).write_text(json.dumps(info), encoding='utf-8')\n"
                "raise SystemExit(0)\n",
                encoding="utf-8",
            )
            job = root / "job.json"
            job.write_text(json.dumps({
                "root": str(root), "result": str(root / "result.json"), "python": sys.executable,
                "src": str(src), "args": [], "control": True, "job": "offline-job",
            }), encoding="utf-8")
            saved = {key: os.environ.get(key) for key in ("STRATA_OFFLINE_ENTRY", "STRATA_NO_ACQUIRE", "PATH")}
            os.environ["STRATA_OFFLINE_ENTRY"] = "1"
            os.environ["STRATA_NO_ACQUIRE"] = "1"
            os.environ["PATH"] = str(chosen) + os.pathsep + "/usr/bin"
            try:
                code = mcp.install_job(str(job))
            finally:
                for key, value in saved.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
            self.assertEqual(code, 0, (root / "result.json").read_text(encoding="utf-8") if (root / "result.json").exists() else "no result")
            info = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(info["offline"], "1")
            self.assertIsNone(info["acquire"])
            self.assertEqual(info["path0"], str(chosen))
            self.assertNotIn(str(other), info["path"])
            self.assertNotIn("opened", info)
            self.assertEqual(info["exit"], 1)

    def test_python_dll_check_matches_the_loader_and_ignores_an_unused_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "python.exe").write_bytes(fake_pe(["KERNEL32.dll", "python312.dll"]))
            (root / "python312.dll").write_bytes(fake_pe(["KERNEL32.dll"]))
            package = root / "Lib" / "site-packages" / "numpy"
            core = package / "_core"
            libs = root / "Lib" / "site-packages" / "numpy.libs"
            core.mkdir(parents=True)
            libs.mkdir()
            (libs / "libscipy_openblas.dll").write_bytes(fake_pe(["KERNEL32.dll"]))
            (package / "__init__.py").write_text(
                "import os\n"
                "libs_dir = os.path.join(os.path.dirname(__file__), os.pardir, 'numpy.libs')\n"
                "os.add_dll_directory(libs_dir)\n",
                encoding="utf-8",
            )
            (core / "_multiarray.pyd").write_bytes(fake_pe(["python312.dll", "libscipy_openblas.dll"]))
            unused = root / "unused"
            unused.mkdir()
            (unused / "secret.dll").write_bytes(fake_pe(["KERNEL32.dll"]))
            (root / "plugin.pyd").write_bytes(fake_pe(["secret.dll"]))
            text = "\n".join(issue.line() for issue in wo.closure_issues(root, python_runtime=True))
            self.assertNotIn("_multiarray.pyd", text)
            self.assertIn("secret.dll", text)
            self.assertIn("plugin.pyd", text)

    def test_engine_child_path_drops_a_cuda_toolkit_under_the_offline_entry(self):
        import serve.server as server
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            engine = root / "engine"
            toolkit = root / "toolkit"
            engine.mkdir()
            toolkit.mkdir()
            (engine / "cublas64_13.dll").write_bytes(b"package")
            (toolkit / "cublas64_12.dll").write_bytes(b"toolkit")
            inherited = os.pathsep.join([str(engine), str(toolkit), "/usr/bin"])
            with mock.patch.dict(os.environ, {"PATH": inherited, "STRATA_OFFLINE_ENTRY": "1"}):
                env = server.child_env({"lib_dirs": [str(engine)]}, offline=False)
            parts = env["PATH"].split(os.pathsep)
            self.assertIn(str(engine), parts)
            self.assertNotIn(str(toolkit), parts)
            self.assertIn("/usr/bin", parts)

    def test_the_engine_server_treats_the_offline_entry_as_offline_without_the_flag(self):
        import serve.server as server
        self.assertFalse(server.offline_requested(False))
        self.assertTrue(server.offline_requested(True))
        with mock.patch.dict(os.environ, {"STRATA_OFFLINE_ENTRY": "1"}):
            self.assertTrue(server.offline_requested(False))

    def test_plan_command_prints_the_address_and_a_missing_folder_exits_5(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "ready"
            model = root / "m.gguf"
            root.mkdir()
            wo.write_header(model, "qwen4exp")
            _tree(root)
            with mock.patch("sys.stdout") as out, \
                    mock.patch.object(wo, "read_nvidia_driver", return_value=("581.80", None)), \
                    mock.patch("strata_control.engine_component.cuda_archs_present", return_value=["sm_89"]):
                code = wo.main(["--root", str(root), "--model", str(model), "--plan"])
            self.assertEqual(code, 0)
            printed = "".join(call.args[0] for call in out.write.call_args_list)
            self.assertIn("http://127.0.0.1:8791/", printed)
            self.assertIn("Windows 10 or Windows 11", printed)
            empty = Path(tmp) / "empty"
            empty.mkdir()
            code = wo.main(["--root", str(empty), "--model", str(model), "--plan"])
            self.assertEqual(code, 5)


def _manifest(folder: Path, variant: str, floor: str, archs: list[str]) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    meta = {"variant": variant, "min_driver": {"nvidia": floor}, "archs": archs, "version": "0.1.55", "backend": "cuda", "platform": "windows-x86_64"}
    (folder / "engine-component.json").write_text(__import__("json").dumps(meta), encoding="utf-8")
    (folder / "strata.exe").write_bytes(fake_pe(["KERNEL32.dll"]))
    return meta


class CudaChoice(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cuda12 = self.root / "components" / "engine" / "0.1.55-windows-x86_64-cuda-cuda12"
        self.cuda13 = self.root / "components" / "engine" / "0.1.55-windows-x86_64-cuda-cuda13"
        _manifest(self.cuda12, "cuda12", "576.02", ["sm_60", "sm_61", "sm_70", "sm_75", "sm_80", "sm_86", "sm_89", "compute_89"])
        _manifest(self.cuda13, "cuda13", "580.65", ["sm_75", "sm_86", "sm_89", "sm_120", "compute_120"])
        self.packages = wo.cuda_packages(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_cuda13_is_preferred_only_when_the_driver_and_gpu_allow_it(self):
        chosen, note = wo.choose_cuda(self.packages, archs=["sm_89"], driver="581.80", manual=None)
        self.assertEqual(chosen["variant"], "cuda13")
        self.assertIn("cuda12", note)
        self.assertNotIn(str(self.cuda12), note)

    def test_an_older_driver_keeps_cuda12_even_if_a_newer_toolkit_could_be_present(self):
        chosen, note = wo.choose_cuda(self.packages, archs=["sm_86"], driver="576.02", manual=None)
        self.assertEqual(chosen["variant"], "cuda12", note)
        refused, why = wo.choose_cuda(self.packages, archs=["sm_86"], driver="576.02", manual="13")
        self.assertIsNone(refused)
        self.assertIn("580.65", why)
        self.assertIn("toolkit", why)

    def test_pascal_does_not_get_cuda13_and_blackwell_prefers_cuda13(self):
        pascal, _ = wo.choose_cuda(self.packages, archs=["sm_61"], driver="595.00", manual=None)
        self.assertEqual(pascal["variant"], "cuda12")
        blackwell, _ = wo.choose_cuda(self.packages, archs=["sm_120"], driver="580.65", manual=None)
        self.assertEqual(blackwell["variant"], "cuda13")

    def test_the_chosen_engine_directory_is_the_only_one_put_on_the_path(self):
        from strata_control.engine_component import package_lib_dirs
        _tree(self.root)
        _manifest(self.cuda12, "cuda12", "576.02", ["sm_60", "sm_86", "sm_89", "compute_89"])
        _manifest(self.cuda13, "cuda13", "580.65", ["sm_75", "sm_86", "sm_89", "sm_120", "compute_120"])
        report = wo.preflight(self.root, driver=("581.80", None), archs=["sm_89"])
        self.assertTrue(report.ok, [issue.line() for issue in report.issues])
        self.assertEqual(report.choice["variant"], "cuda13")
        dirs = package_lib_dirs(report.choice["folder"])
        self.assertIn(str(self.cuda13), dirs)
        self.assertNotIn(str(self.cuda12), dirs)
        model = self.root / "模型 目录" / "qwen.gguf"
        model.parent.mkdir()
        wo.write_header(model, "qwen4exp")
        seen = {}

        def spawn(argv, env=None, cwd=None):
            seen["argv"], seen["env"], seen["cwd"] = argv, env, cwd
            return mock.Mock()

        started = wo.begin(self.root, model, check_driver=True, driver_info=("581.80", None), archs=["sm_89"], spawn=spawn)
        self.assertEqual(started.env["PATH"].split(os.pathsep)[0], str(self.cuda13))
        self.assertNotIn(str(self.cuda12), started.env["PATH"])
        self.assertEqual(started.env["STRATA_OFFLINE_CUDA"], "13")
        self.assertEqual(seen["env"]["STRATA_OFFLINE_MODEL"], str(model.parent.resolve()))
        self.assertEqual(seen["env"]["STRATA_OFFLINE_ENTRY"], "1")
        self.assertEqual(seen["env"]["PATH"].split(os.pathsep)[0], str(self.cuda13))

    def test_unknown_compute_capability_keeps_cuda12_and_does_not_guess_cuda13(self):
        chosen, note = wo.choose_cuda(self.packages, archs=[], driver="581.80", manual=None)
        self.assertEqual(chosen["variant"], "cuda12", note)
        self.assertIn("cuda13", note)
        only13 = [item for item in self.packages if item["variant"] == "cuda13"]
        refused, why = wo.choose_cuda(only13, archs=[], driver="581.80", manual=None)
        self.assertIsNone(refused, why)
        self.assertIn("sm_75", why)
        self.assertIn("Nothing was selected", why)
        manual, told = wo.choose_cuda(self.packages, archs=[], driver="581.80", manual="13")
        self.assertEqual(manual["variant"], "cuda13")
        self.assertIn("not reported", told)
        known, known_note = wo.choose_cuda(self.packages, archs=["sm_89"], driver="581.80", manual=None)
        self.assertEqual(known["variant"], "cuda13", known_note)

    def test_the_spawned_path_drops_a_cuda_toolkit_directory(self):
        toolkit = self.root / "toolkit" / "bin"
        toolkit.mkdir(parents=True)
        (toolkit / "cublas64_13.dll").write_bytes(b"toolkit")
        (self.cuda12 / "cudart64_12.dll").write_bytes(b"other-engine")
        model = self.root / "模型 目录" / "qwen.gguf"
        model.parent.mkdir(exist_ok=True)
        wo.write_header(model, "qwen4exp")
        _tree(self.root)
        _manifest(self.cuda12, "cuda12", "576.02", ["sm_60", "sm_86", "sm_89", "compute_89"])
        _manifest(self.cuda13, "cuda13", "580.65", ["sm_75", "sm_86", "sm_89", "sm_120", "compute_120"])
        (self.cuda12 / "cudart64_12.dll").write_bytes(b"other-engine")
        inherited = os.pathsep.join([str(toolkit), str(self.cuda12), "/usr/bin"])
        seen = {}

        def spawn(argv, env=None, cwd=None):
            seen["env"] = env
            return mock.Mock()

        with mock.patch.dict(os.environ, {"PATH": inherited}):
            started = wo.begin(self.root, model, check_driver=True, driver_info=("581.80", None), archs=["sm_120"], spawn=spawn)
        path = started.env["PATH"].split(os.pathsep)
        self.assertEqual(path[0], str(self.cuda13))
        self.assertNotIn(str(toolkit), path)
        self.assertNotIn(str(self.cuda12), path)
        self.assertIn("/usr/bin", path)
        self.assertEqual(seen["env"]["STRATA_OFFLINE_MODEL"], str(model.parent.resolve()))
        self.assertEqual(seen["env"]["STRATA_OFFLINE_ENTRY"], "1")

    def test_a_missing_driver_is_an_error_and_does_not_assume_cuda13(self):
        report = wo.preflight(self.root, driver=(None, "nvidia-smi was not found"))
        self.assertFalse(report.ok)
        text = "\n".join(issue.line() for issue in report.issues)
        self.assertIn("580.65", text)
        self.assertIn("576.02", text)
        self.assertIn("toolkit", text)


if __name__ == "__main__":
    unittest.main()
