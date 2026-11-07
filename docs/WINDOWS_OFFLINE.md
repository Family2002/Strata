# Windows offline entry

This is a separate way to open Strata on Windows. It does not use the desktop installer and it does not
use the WebView2 window. The page is the same App Host, opened in a browser that is already on the PC.

`strata launch`, the desktop app and `strata update` are unchanged. This command is opt-in.

## 怎么用

准备文件夹的工作发生在有这些文件的机器上。要运行的那台 Windows 电脑只执行最后一条命令。

1. 准备目录里要有这些本地文件（少一个就退出，并写出缺的路径；不会下载，也不会打开安装程序）：
   - `node/node.exe`，以及它的导入表里不属于 Windows 和显卡驱动的 DLL（官方 Node 的 Windows 压缩包里，这包含 `vcruntime140.dll`。用 `python packaging/node_runtime.py --zip <已有的 node zip> --dest <目录>/node` 按导入表解出，不要只复制 `node.exe`）
   - `host/server/index.mjs` 和已经构建好的 `host/dist/index.html`
   - `control/python/python.exe`、它导入的 DLL、`control/strata.py`、`control/strata_control/`
   - `components/engine/<包>/engine-component.json` 和该包里的 `strata.exe` 及其 DLL（Qwen）。DeepSeek-V4.1 还要 `components/engine-dsv41/<包>/strata-dsv41.exe` 及其 DLL
   - `third_party/llama.cpp/gguf-py/` 和 `third_party/llama.cpp/ggml/CMakeLists.txt`（这是准备工具用的本地源码树标记。离线入口不会运行 cmake、git 或编译器）
   - 已经在本机的 `.gguf`。能本地识别的架构只有 `qwen4exp`（Qwen3.8-Flash-Next）和 `deepseek41`（DeepSeek-V4.1-Flash）。其他架构、不是 GGUF 的文件、mmproj 和辅助表会被拒绝
2. 那台 Windows 电脑上还要有：Windows 10 或 11 的 x64，以及这个引擎包对应的显卡驱动。程序不安装它们。不需要另外安装 Python、Node、Visual C++ 运行库、WebView2 或 CUDA Toolkit；那些 DLL 必须已经在上面的目录里，否则依赖闭包检查失败。
3. 检查并打印地址，不启动：

```text
strata offline --root D:\StrataOffline --model D:\模型\qwen.gguf --plan
```

4. 启动本地页面（只监听 127.0.0.1）：

```text
strata offline --root D:\StrataOffline --model D:\模型\qwen.gguf
```

用这台电脑上已经有的浏览器打开打印出来的地址。这条命令不安装浏览器。

## What a missing file does

The process prints the missing path and exits with status 5. It does not offer a download, a winget
install, pip, git, cmake, or the WebView2 installer. The same refusal is on the setup download path,
the engine update path and the host's update buttons while `STRATA_OFFLINE_ENTRY=1`.

## What was not run

No Windows machine and no GPU were used to write this. Starting the page, loading a model and
inference on Windows are not claimed here. The automated tests build a fake Windows folder (including
paths with spaces and Chinese characters) and check that the start command is assembled without a
network call. A workflow file runs those tests on `ubuntu-latest` and `windows-latest`; it does not
build an installer and it does not download a model.
