# Windows offline entry

This is a separate way to open Strata on Windows. It does not use the desktop installer and it does not
use the WebView2 window. The page is the same App Host, opened in a browser that is already on the PC.

`strata launch`, the desktop app and `strata update` are unchanged. This command is opt-in.

## 安装包

`packaging/build_windows_offline_installer.py` builds one NSIS installer that contains both
upstream engine packages from Niko1221/Strata v0.2.9 (engine 0.1.55):

- `0.1.55-windows-x86_64-cuda-cuda12`, manifest driver floor NVIDIA 576.02, code sm_60 through sm_89 plus compute_89 PTX
- `0.1.55-windows-x86_64-cuda-cuda13`, manifest driver floor NVIDIA 580.65, code sm_75, sm_86, sm_89, sm_120 plus compute_120 PTX

Each package stays in its own directory. At start, only the chosen directory is put on PATH.
CUDA 13 is used when the GPU and the driver both meet its manifest. An installed CUDA toolkit is not read.
`Start-Strata.cmd --cuda 12` or `--cuda 13` forces one package; a choice the manifest rejects exits instead of switching.

The first built file, `Strata-0.2.9-offline-windows-x64-cuda12-cuda13-setup.exe` (1672831334 bytes,
SHA-256 2c4ec5321ac73a56857a145607a57451328575a42823a119535fed957518ca00), is the old package from
`bbb0ceb8`. It does not contain the later fixes. The corrected installer is
`Strata-0.2.9-offline-r2-windows-x64-cuda12-cuda13-setup.exe`. Its SHA-256 is the file
`offline-r2/*.sha256` next to the parts, and `OFFLINE_BUILD.txt` inside the package names the
commit that was packed. It is built on Linux with makensis. It is not run on Windows here, and no
GPU inference is run.

When nvidia-smi does not report a compute capability, the start does not guess CUDA 13. It uses
CUDA 12, the package that still includes the older card codes. If CUDA 13 is the only package and
the card code is missing, it exits. A CUDA toolkit directory is removed from PATH for the host and
for the engine process. The Python DLL check counts a DLL beside the file, beside python.exe, or
in a directory that package adds with `os.add_dll_directory` (NumPy's `numpy.libs`). A DLL that
only exists in some other subdirectory does not count. The page does not open a tunnel, turn on
network sharing, download an image model, or run web search while this entry is set.

## 怎么用

安装包会自己带上 Node、Control 里的 Python、两套引擎和页面。要运行的那台 Windows 电脑只安装并打开它。

1. 安装 `Strata-0.2.9-offline-windows-x64-cuda12-cuda13-setup.exe`。它不访问网络，也不安装别的运行库。
2. 运行 `%LOCALAPPDATA%\StrataOffline\Start-Strata.cmd`。
3. 用已经装在这台电脑上的浏览器打开它打印的 `http://127.0.0.1:8791/`。
4. 在页面里选择服务器上已经下好的 GGUF，或启动时加上 `--model` 指向那个文件或文件夹。当前源码会把这个路径交给 host，安装任务把它当作本地 `gguf_dir`，不再下载权重。本包装进的准备工具只处理 `qwen4exp`。没有本地草稿层或图片编码器时，这两项保持关闭，不会下载。

这台电脑仍要有 Windows 10 或 11 x64，以及清单所写的 NVIDIA 驱动。程序不安装驱动。

## What a missing file does

The process prints the missing path and exits with status 5. It does not offer a download, a winget
install, pip, git, cmake, or the WebView2 installer. The same refusal is on the setup download path,
the engine update path and the host's update buttons while `STRATA_OFFLINE_ENTRY=1`.

## 已交付安装包里没有的修复

上面的 SHA-256 对应已经交给用户的安装包（源码 `bbb0ceb8`）。审查之后源码又改了三处，这些改动不在那个文件里：

- `strata offline --model` 会把文件夹放进 host 的 `STRATA_OFFLINE_MODEL`，安装请求没有自带 `gguf_dir` 时使用它。
- 安装任务清掉 `STRATA_NO_ACQUIRE` 之后，`setup.py` 的全部 http(s) 请求（不只是 `download()`）仍会拒绝。
- 引擎服务看到 `STRATA_OFFLINE_ENTRY=1` 时按 `--offline` 处理，远程图片地址和 MCP 子进程不会因 `runtime start` 没带这个参数而放行。网页搜索同样不发起请求。

没有为这些修复重新做安装包。继续使用已交付的 exe，就是在用没有这三处的程序。

## What was not run

No Windows machine and no GPU were used to write this. Starting the page, loading a model and
inference on Windows are not claimed here. The automated tests build a fake Windows folder (including
paths with spaces and Chinese characters) and check that the start command is assembled without a
network call. A workflow file runs those tests on `ubuntu-latest` and `windows-latest`; it does not
build an installer and it does not download a model.
