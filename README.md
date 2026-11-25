# XFY-phone-use

让支持 MCP 的助手在 Android 平板上使用一块独立的虚拟屏。助手可以观察界面、点按钮、滑动和打开应用。人在主屏上继续使用其他应用，也可以从电脑只读观看助手正在操作的那块屏。

`xfy-phone-display` 在平板上创建副屏，`xfy-phone-use` 通过标准输入输出（stdio）向 MCP 客户端提供 7 个工具。可以单独接入自己的助手。项目最初用于小肥鱼，已在 Android 13、ARM64 Debian chroot 上实测。

## 运行环境

- 已获取 root 的 Android 设备，配有 Debian chroot。chroot 需要看得到 Android 的 PID 1、挂载命名空间和抽象 Unix 套接字。操作端需要有权限运行 `nsenter`。这是面向已有这类环境的发行包。
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

**2. 在平板的 Debian chroot 内安装。** 以下命令以 root 执行。Release 的 wheel 可直接安装，Python 虚拟环境会放在 `/opt/xfy-phone-venv`。[下载发行包](https://github.com/ShuangjieGao/XFY-phone-use/releases/tag/v0.1.0)

```sh
apt-get update
apt-get install -y python3-venv util-linux ffmpeg tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng
python3 -m venv /opt/xfy-phone-venv
/opt/xfy-phone-venv/bin/python -m pip install 'xfy-phone-use[vision] @ https://github.com/ShuangjieGao/XFY-phone-use/releases/download/v0.1.0/xfy_phone_use-0.1.0-py3-none-any.whl'
```

从源码安装时，在仓库目录运行 `/opt/xfy-phone-venv/bin/python -m pip install '.[vision]'`。

**3. 在平板上启动副屏。** 示例用 `com.android.settings` 打开系统设置。也可以将包名换为设备上已安装、且能在副屏运行的应用。先把主屏切到另一个应用，保持主屏前台可识别。

```sh
/opt/xfy-phone-venv/bin/xfy-phone-display run \
  --server-jar /data/local/tmp/scrcpy-server-v4.1.jar \
  --package com.android.settings --width 900 --height 1600 --density 360
```

保持这个进程运行。启动成功后终端输出含 `id`、`width` 和 `height` 的 JSON，随后模型即可操作。`--server-jar` 填从 Android 一侧看到的路径。要释放虚拟屏，可以在另一个 Debian 终端运行 `/opt/xfy-phone-venv/bin/xfy-phone-display stop`。

## 连接 MCP 客户端

以下示例适用于在**同一平板 chroot 内**启动 stdio 子进程、支持 `mcpServers` 配置的客户端。其他客户端使用同一个可执行文件；若更改 `XFY_PHONE_RUNTIME`，请让副屏运行时和 MCP 使用同一目录。默认运行时目录和 Unix 套接字仅供创建它们的用户访问。

```json
{
  "mcpServers": {
    "xfy-phone-use": {
      "command": "/opt/xfy-phone-venv/bin/xfy-phone-use",
      "env": { "XFY_PHONE_RUNTIME": "/run/xfy-phone-use" }
    }
  }
}
```

第一次可以让助手执行「查看副屏状态，告诉我屏幕尺寸」。确认 `phone_status` 返回 `ready: true` 后，再试「打开计算器，看看角度单位，切换一次并读回结果」。点击与滑动使用刚刚观察到的画面编号；每次操作后重新观察即可确认效果。

| MCP 工具 | 用途 |
| --- | --- |
| `phone_status` | 查看副屏是否就绪、尺寸和可识别的前台活动 |
| `phone_launch` | 根据 Android 包名在副屏启动应用 |
| `phone_observe` | 通过 OCR 返回文字区域和 `observation_id`，可选 `screenshot: true` 取图或 `elements: false` 跳过 OCR |
| `phone_tap` | 按文字区域 ID 或像素坐标点按 |
| `phone_swipe` | 在观察到的画面上滑动 |
| `phone_type` | 向输入焦点输入可打印 ASCII 文本。中文可由 OCR 读取，直接中文输入尚未实现 |
| `phone_key` | 发送 `back`、`enter` 或 `del` |

## 从电脑看同一块屏

在平板 Debian 用 `cat /run/xfy-phone-use/display.json` 查本次的 `id`。假设返回 `7`，在装有 scrcpy 4.1 的电脑上执行：

```sh
scrcpy --display-id=7 --no-control --no-audio --no-power-on
```

关闭这个只读窗口后，平板上的副屏继续运行。部分 Android 版本会在操作副屏时短暂转移全局输入焦点，应用对副屏的支持也可能不同。若状态显示 `Primary foreground could not be verified`，先回到可辨认的主屏应用，再执行 `phone_status`。若动作报告画面已变，重新 `phone_observe` 后再定位。

## 开发与许可

```sh
python -m unittest discover -s tests -v
```

本项目按 [GNU AGPL-3.0-only](LICENSE) 开源。scrcpy、FFmpeg、Tesseract 和 Pillow 独立安装，适用各自的上游许可证。
