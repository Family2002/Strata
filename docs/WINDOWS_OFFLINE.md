# Windows offline entry

This is a separate way to open Strata on Windows. It does not use the desktop installer and it does not
use the WebView2 window. The page is the same App Host, opened in a browser that is already on the PC.

`strata launch`, the desktop app and `strata update` are unchanged. This command is opt-in.

## 安装包

This branch is upstream Niko1221/Strata tag v0.2.11, commit `c3b3f5035506f3e966f1b4b7b188aeaabae92f1d`
(app and Terminal 0.2.11, Control and engines 0.1.57), plus the offline entry. It is not a version
number written onto the 0.2.9 tree.

`packaging/build_windows_offline_installer.py` packs the v0.2.11 Windows release files:

- Control `strata-control-0.1.57-windows-x86_64.zip`, SHA-256 `e93caba92fd69398fa18e4e698c21e798e2e488c2d7ddb77b5272bec6d347d8f`
- CUDA 12 engine `0.1.57-windows-x86_64-cuda-cuda12`, release SHA-256 `2fb05b837c9625bb5e9422827f98ffd2a633c54e50dd51cd2db14320852686fb`, manifest driver floor NVIDIA 576.02, code sm_60 through sm_89 plus compute_89 PTX, build id `0bdcef4682c5+cuda12.9`
- CUDA 13 engine `0.1.57-windows-x86_64-cuda-cuda13`, release SHA-256 `0cdf191f0fb39b90edbb902f4d096b90f2ee90a4542deb8aef24985b22407c0f`, manifest driver floor NVIDIA 580.65, code sm_75, sm_86, sm_89, sm_120 plus compute_120 PTX, build id `0bdcef4682c5+cuda13.0`

The tag message says those Qwen engines were built from `0bdcef468`. Their `version` field is 0.1.57.
The notes string inside each manifest still says "0.2.9 NVIDIA"; that text is upstream's, and the
files are the v0.2.11 release assets, not the 0.1.55 packages. Node 24.12.0 and llama.cpp
`3cf03257` are still the pins in this commit. `serve/server.py` in the installer is this checkout,
not the copy inside the Control zip.

Each engine stays in its own directory. At start, only the chosen directory is put on PATH, and a
directory that contains CUDA toolkit DLLs is removed. CUDA 13 is used when the GPU code was read
and both the card and the driver meet its manifest. If the compute capability was not reported,
CUDA 13 is not guessed. `Start-Strata.cmd --cuda 12` or `--cuda 13` forces one package; a choice
the manifest rejects exits instead of switching.

The 0.2.11 installer is `Strata-0.2.11-offline-r1-windows-x64-cuda12-cuda13-setup.exe`
(1690656188 bytes, SHA-256 `f5bfbbccc3ecb93264ac0a370d3e7fcd5adccb8f892f334c451393916e7455ce`).
`OFFLINE_BUILD.txt` inside it names commit `25987764a054e77c0dbcb29969628c12d19abc96`. It was built
on Linux with makensis and unpacked with 7-Zip. It was not run on Windows, and no GPU inference
was run. 68 parts of at most 24MiB are under `offline-v0.2.11/` and sum to the same byte count.

The earlier package `Strata-0.2.9-offline-r2-windows-x64-cuda12-cuda13-setup.exe` (1672834165 bytes,
SHA-256 `6a0c2297e799932a25920a35f312eac3419d69547aacdee81d9c79e1d4fd5a48`, commit `12275911`) is
left in place under `offline-r2/`. It is engine 0.1.55 from the v0.2.9 release.

When nvidia-smi does not report a compute capability, the start does not guess CUDA 13. It uses
CUDA 12, the package that still includes the older card codes. If CUDA 13 is the only package and
the card code is missing, it exits. A CUDA toolkit directory is removed from PATH for the host and
for the engine process. The Python DLL check counts a DLL beside the file, beside python.exe, or
in a directory that package adds with `os.add_dll_directory` (NumPy's `numpy.libs`). A DLL that
only exists in some other subdirectory does not count. The page does not open a tunnel, turn on
network sharing, download an image model, or run web search while this entry is set.

## 怎么用

安装包会自己带上 Node、Control 里的 Python、两套引擎和页面。要运行的那台 Windows 电脑只安装并打开它。

1. 安装 `Strata-0.2.11-offline-r1-windows-x64-cuda12-cuda13-setup.exe`。它不访问网络，也不安装别的运行库。这是 0.2.11 / 引擎 0.1.57。已经上传的 0.2.9 offline-r2 包不要被这个文件覆盖。
2. 运行 `%LOCALAPPDATA%\StrataOffline\Start-Strata.cmd`。
3. 用已经装在这台电脑上的浏览器打开它打印的 `http://127.0.0.1:8791/`。
4. 在页面里选择服务器上已经下好的 GGUF，或启动时加上 `--model` 指向那个文件或文件夹。当前源码会把这个路径交给 host，安装任务把它当作本地 `gguf_dir`，不再下载权重。本包装进的准备工具只处理 `qwen4exp`。没有本地草稿层或图片编码器时，这两项保持关闭，不会下载。

这台电脑仍要有 Windows 10 或 11 x64，以及清单所写的 NVIDIA 驱动。程序不安装驱动。

## What a missing file does

The process prints the missing path and exits with status 5. It does not offer a download, a winget
install, pip, git, cmake, or the WebView2 installer. The same refusal is on the setup download path,
the engine update path and the host's update buttons while `STRATA_OFFLINE_ENTRY=1`.

## 旧包和这一版

0.2.9 的两份安装包都留着，没有被这次覆盖：

- 最早的包：1672831334 字节，SHA-256 `2c4ec5321ac73a56857a145607a57451328575a42823a119535fed957518ca00`，源码 `bbb0ceb8`，引擎 0.1.55。
- offline-r2：1672834165 字节，SHA-256 `6a0c2297e799932a25920a35f312eac3419d69547aacdee81d9c79e1d4fd5a48`，包内提交 `12275911`，引擎 0.1.55。

0.2.11 的 offline-r1 用的是引擎 0.1.57。包内提交是 `25987764`。记下哈希的文档提交不在安装包里。

离线行为和 r2 相同，并且是在 0.2.11 的准备逻辑上接上的：`--model` 进入 `gguf_dir`；http(s) 在打开前拒绝；引擎把离线入口当成 `--offline`；网页搜索、隧道、局域网分享和图片模型下载不发起请求；没读到计算能力时不选 CUDA 13；PATH 里的 CUDA Toolkit 目录会被去掉。

## What was not run

No Windows machine and no GPU were used to write this. Starting the page, loading a model and
inference on Windows are not claimed here. The automated tests build a fake Windows folder (including
paths with spaces and Chinese characters) and check that the start command is assembled without a
network call. A workflow file runs those tests on `ubuntu-latest` and `windows-latest`; it does not
build an installer and it does not download a model.
