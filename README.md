# phone-use

[English](README.en.md) · [源码安装](#安装) · [连接助手](#连接-mcp-客户端) · [输入中文](#输入中文)

**在安卓主屏玩《星露谷物语》，让助手在副屏处理另一件事。** `phone-use` 提供 7 个 MCP 工具，供助手观察并操作独立的安卓虚拟显示器。支持 stdio，已在 root 的 Android 13 平板和 ARM64 Debian chroot 上使用。

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/images/stardew-jerry-zh-poster.png">
  <img src="docs/images/stardew-jerry-zh.gif" alt="同一台平板的完整双屏实录：主屏运行安卓版星露谷，副屏在微信好友私聊里发送中文草稿，随后显示一条绿色消息气泡。头像与旧消息中的隐私区域已遮挡。">
</picture>

这段 34 秒实录从已写好的中文草稿开始：助手核对收件人与正文后点击「发送」，微信显示「买种子需要四百八十金」。两块屏幕保留完整画幅；公开画面局部遮挡头像、旧附件名和部分旧内容。[观看好友私聊发送实录](docs/images/stardew-jerry-zh.mp4) · [放大查看发送结果](docs/images/stardew-jerry-zh-poster.png)

| 主屏继续玩游戏时 | 副屏在做什么 | 全幅原速演示 |
| --- | --- | --- |
| 在农场里走动 | 小肥鱼在微信文件传输助手里输入「买种子需要四百八十金」，从编辑框读回文字后点一次「发送」。公开画面仅遮住发送者头像。 | [中文发信 · 43 秒](docs/images/wechat-filehelper-zh.mp4) · [原尺寸截图](docs/images/wechat-filehelper-zh-poster.png) |
| 在农舍前走向田地 | 助手在微信里点「确定」，将一份模拟种子采购清单保存到收藏。清单通过安卓分享传入，全程不涉及好友聊天。 | [微信实录 · 24 秒](docs/images/stardew-wechat.mp4) · [原尺寸截图](docs/images/stardew-wechat-poster.png) |
| 在农场里移动 | 助手打开计算器，随后演示脚本通过 MCP 输入 `24 × 20`，读回种子预算 **480 金**。 | [计算器实录 · 23 秒](docs/images/stardew-fullscreen.mp4) · [原尺寸截图](docs/images/stardew-fullscreen-poster.png) |

每段都是同一台设备的主屏与副屏同步采集，保留两块屏的完整画面和连续时间线。

| 使用时你关心什么 | phone-use 怎样处理 |
| --- | --- |
| 我正占着平板屏幕 | `phone-display` 创建独立的 Android 显示器。启动应用、点按和滑动都指定副屏编号。不同系统可能短暂转移输入焦点。 |
| 助手怎样知道点哪里 | 默认通过中英文 OCR 获取文字和所在区域。按图标时可按需取截图；指定 `elements: false` 可以跳过 OCR。 |
| 我怎么知道它点成功了 | 点按绑定最近一次观察。界面或目标已变化时会要求重看；助手通过下一帧里的文字或图像核实操作结果。 |

## 计算器演示

连接 MCP 后，让助手试一句：**「在副屏打开计算器，算 24 包种子、每包 20 金一共要多少，再读一遍屏幕上的结果。」** 助手可以通过 `phone_observe` 确认显示的是 `480`。不同设备的应用包名和按键位置可能不同。

## 运行环境

- 已获取 root 的 Android 设备，配有 Debian chroot。chroot 需要看得到 Android 的 PID 1、挂载命名空间和抽象 Unix 套接字。操作端需要有权限运行 `nsenter`。
- Python 3.9 或更高版本、`util-linux`、FFmpeg（支持 `-fps_mode`）和 **scrcpy-server 4.1**。
- 用 OCR 定位文字时，安装 Tesseract 及简体中文、英文语言包。Pillow 可增强对局部动画的处理，使点击前能核对按钮所在区域。

## 安装

**1. 在电脑上放置 scrcpy 服务端。** 下载官方 `scrcpy-server-v4.1`，校验 SHA-256 后放到 Android。下面的 PowerShell 命令假设电脑已经能用 `adb devices` 看到目标平板。如果连接了多台设备，给 `adb` 加 `-s <目标序列号>`。[scrcpy 4.1 官方发行页](https://github.com/Genymobile/scrcpy/releases/tag/v4.1)

```powershell
curl.exe -fL -o scrcpy-server-v4.1 https://github.com/Genymobile/scrcpy/releases/download/v4.1/scrcpy-server-v4.1
$expected = 'deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae'
if ((Get-FileHash .\scrcpy-server-v4.1 -Algorithm SHA256).Hash -ne $expected) { throw 'scrcpy-server checksum mismatch' }
adb push scrcpy-server-v4.1 /data/local/tmp/scrcpy-server-v4.1.jar
```

**2. 在平板的 Debian chroot 内从源码安装。** 下面的命令以 root 执行。已有源码目录的用户可以直接进入该目录。

```sh
apt-get update
apt-get install -y git python3-venv util-linux ffmpeg tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng
git clone https://github.com/ShuangjieGao/phone-use.git
cd phone-use
python3 -m venv /opt/xfy-phone-venv
/opt/xfy-phone-venv/bin/python -m pip install '.[vision]'
```

**3. 在平板上启动副屏。** 示例用 `com.android.settings` 打开系统设置。也可以将包名换为设备上已安装、且能在副屏运行的应用。先把主屏切到另一个应用，保持主屏前台可识别。

```sh
/opt/xfy-phone-venv/bin/phone-display run \
  --server-jar /data/local/tmp/scrcpy-server-v4.1.jar \
  --package com.android.settings --width 900 --height 1600 --density 360
```

保持这个进程运行。启动成功后终端输出含 `id`、`width` 和 `height` 的 JSON。`--server-jar` 填从 Android 一侧看到的路径。要释放虚拟屏，可以在另一个 Debian 终端运行 `/opt/xfy-phone-venv/bin/phone-display stop`。

### 验证副屏连接

在平板的第二个 Debian 终端里，从本仓库根目录运行下面的命令。

```sh
python3 examples/check.py --server /opt/xfy-phone-venv/bin/phone-use
```

看到 `Display  ready` 和 `Frame    received` 就可以连接助手。如果提示 `primary foreground unverified`，请解锁设备，让主屏停在一个应用里，再运行同一条命令。

## 连接 MCP 客户端

以下示例适用于在**同一平板 chroot 内**启动 stdio 子进程、支持 `mcpServers` 配置的客户端。用创建副屏的同一个系统用户启动客户端，确保它可以访问运行目录和 Unix 套接字。如果自定义了 `XFY_PHONE_RUNTIME`，请给副屏进程和 MCP 客户端配置相同的值。

```json
{
  "mcpServers": {
    "phone-use": {
      "command": "/opt/xfy-phone-venv/bin/phone-use",
      "env": { "XFY_PHONE_RUNTIME": "/run/xfy-phone-use" }
    }
  }
}
```

第一次可以问助手「查看副屏状态，告诉我屏幕尺寸」。`phone_status` 返回 `ready: true` 后就可以试[计算器演示](#计算器演示)。

### 输入中文

在 Android 11 或更高版本上，可以安装本项目的[原生编辑辅助程序](android-text-input/README.md)。在有 JDK、Android SDK Platform 35 和 Build Tools 的 Linux 构建机上，从仓库根目录运行以下命令。Windows 用户也可以在 WSL 构建，再用已连接平板的 ADB 安装生成的 APK。

```sh
# ANDROID_HOME 指向已安装的 Android SDK
sh android-text-input/build-apk.sh
adb install -r android-text-input/phone-use-text-input.apk
```

在上方 MCP 配置的 `env` 中添加 `"PHONE_USE_TEXT_HELPER": "1"`，重启 MCP 客户端连接。Android 13 及以上的部分应用（包括实测的微信）不会在副屏提供可编辑的无障碍节点。这种情况下，可在安卓的「无障碍 → 已安装或已下载的服务」中手动开启 **phone-use local text input**。这是系统级授权；辅助程序使用当前编辑器的输入连接，不需要更换默认键盘或使用剪贴板。可随时从同一处关闭。

先点中副屏输入框，调用 `phone_observe(editor=true, elements=false)`，再把这次的 `observation_id` 交给 `phone_type`。工具返回 `readback: true` 表示已从同一个编辑框读回预期的文字。收到 `unconfirmed` 时先检查现有文字，再决定是否重试。聊天里的「发送」是单独的一次点按。

### 工具一览

| MCP 工具 | 用途 |
| --- | --- |
| `phone_status` | 查看副屏是否就绪、尺寸和可识别的前台活动 |
| `phone_launch` | 根据 Android 包名在副屏启动应用 |
| `phone_observe` | 通过 OCR 返回文字区域和 `observation_id`，可选 `screenshot: true` 取图或 `elements: false` 跳过 OCR |
| `phone_tap` | 按文字区域 ID 或像素坐标点按 |
| `phone_swipe` | 在观察到的画面上滑动 |
| `phone_type` | 向聚焦的输入框写入单行文本。原生编辑辅助支持中文及读回确认；基础键盘模式支持可打印 ASCII |
| `phone_key` | 发送 `back`、`enter` 或 `del` |

## 从电脑看同一块屏

在平板 Debian 用 `cat /run/xfy-phone-use/display.json` 查本次的 `id`。假设返回 `7`，在装有 scrcpy 4.1 的电脑上执行：

```sh
scrcpy --display-id=7 --no-control --no-audio --no-power-on
```

关闭这个只读窗口后，平板上的副屏继续运行。若状态显示 `Primary foreground could not be verified`，先回到可辨认的主屏应用，再执行 `phone_status`。若动作报告画面已变，重新 `phone_observe` 后再定位。

### 从旧包名迁移

如果虚拟环境中装过旧包名 `xfy-phone-use`，请先在该环境里运行 `python -m pip uninstall xfy-phone-use`，然后从本仓库执行 `python -m pip install '.[vision]'`。新旧包名共用 Python 导入路径。

## 开发与许可

```sh
python -m unittest discover -s tests -v
```

本项目按 [GNU AGPL-3.0-only](LICENSE) 开源。scrcpy、FFmpeg、Tesseract 和 Pillow 独立安装，适用各自的上游许可证。

如果你也在手机或平板上跑通了，欢迎在 [Issues](https://github.com/ShuangjieGao/phone-use/issues) 分享设备型号、Android 版本和能够在副屏运行的应用。觉得这个工具有用，可以点 **Star** 收藏。
