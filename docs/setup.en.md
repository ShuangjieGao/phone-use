# Installation and usage

[Project home](../README.en.md) · [简体中文](setup.md)

## Quick start

You need a rooted Android device with a Debian chroot that can access Android's PID 1, mount namespace and abstract Unix sockets. The chroot needs Python 3.9+, permission to run `nsenter`, and FFmpeg with `-fps_mode` support. Use **scrcpy-server 4.1** with phone-use.

**1. Put scrcpy-server on the Android device.** On a Windows PC with ADB connected, run the following in PowerShell. If multiple devices are connected, add `-s <serial>` to the ADB command. The checksum is for the official 4.1 server. [Upstream release](https://github.com/Genymobile/scrcpy/releases/tag/v4.1)

```powershell
curl.exe -fL -o scrcpy-server-v4.1 https://github.com/Genymobile/scrcpy/releases/download/v4.1/scrcpy-server-v4.1
$expected = 'deacb991ed2509715160ffdc7907e47b4160eb30d1566217e9047fd5b8850cae'
if ((Get-FileHash .\scrcpy-server-v4.1 -Algorithm SHA256).Hash -ne $expected) { throw 'scrcpy-server checksum mismatch' }
adb push scrcpy-server-v4.1 /data/local/tmp/scrcpy-server-v4.1.jar
```

**2. Install from source in the tablet's Debian chroot.** Run as root. If you already have a checkout, use that directory.

```sh
apt-get update
apt-get install -y git python3-venv util-linux ffmpeg tesseract-ocr tesseract-ocr-chi-sim tesseract-ocr-eng
git clone https://github.com/ShuangjieGao/phone-use.git
cd phone-use
python3 -m venv /opt/xfy-phone-venv
/opt/xfy-phone-venv/bin/python -m pip install '.[vision]'
```

If this venv still contains the old package named `xfy-phone-use`, uninstall that package before installing from the checkout. Both package names share a Python import path.

**3. Start the display.** Leave a different app in the foreground on the main display. This example opens Settings on the virtual display. Replace the package name with the Android app you want to use.

```sh
/opt/xfy-phone-venv/bin/phone-display run \
  --server-jar /data/local/tmp/scrcpy-server-v4.1.jar \
  --package com.android.settings --width 900 --height 1600 --density 360
```

The command prints a display ID and dimensions when ready. Leave it running.

## Connect your MCP client

Configure the client to launch this stdio server inside the same chroot, under the same UID as the display runtime. Both processes must use the same private socket directory.

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

### Type Chinese text

On Android 11 or later, install the optional [native editor helper](../android-text-input/README.md). From the checkout on a Linux build host with a JDK, Android SDK Platform 35 and Build Tools, run:

```sh
# Set ANDROID_HOME to your installed Android SDK.
sh android-text-input/build-apk.sh
adb install -r android-text-input/phone-use-text-input.apk
```

You can also build in WSL and install the resulting APK with the ADB connected to your tablet. Add `"PHONE_USE_TEXT_HELPER": "1"` to the MCP client's `env` and reconnect. On Android 13 or later, an app may hide its editable accessibility nodes on a virtual display, as WeChat does on the tested tablet. If necessary, enable **phone-use local text input** in Android's Accessibility settings under installed services. This is a system-level grant. The helper uses the editor's input connection, leaves your default keyboard in place and does not use the clipboard. You can disable it in the same settings.

Focus the field, call `phone_observe(editor=true, elements=false)`, and pass that observation ID to `phone_type`. A `readback: true` result means the helper read the expected text back from the same editor. If the result is `unconfirmed`, inspect the current text before retrying. Sending a chat message is a separate tap.

## Check the connection

With the display running, execute this from the repository root in the chroot. It checks the MCP server and display without tapping anything.

```sh
python3 examples/check.py --server /opt/xfy-phone-venv/bin/phone-use
```

You can also ask your MCP client to call `phone_status` and report the display ID and dimensions. Look for `ready: true` before trying an input.

Look for `Display  ready` and `Frame    received`. If you see `primary foreground unverified`, unlock the device, leave an app open on the main display and run the check again.

## Try it with a calculator

> Open Calculator on the virtual display. Enter the cost of 24 seed packets at 20 gold each and read back the result.

Look for `480` on the virtual display. On the test tablet, Calculator's package is `com.miui.calculator`. Use the package installed on your device.

## Tools

When polling a page, pass your last `observation_id` as `since` to `phone_observe`. On `unchanged: true`, reuse the visible content you already received and use the **new ID** for your next input. Changed frames return the requested text regions or image. For typing, `editor=true, elements=false` binds the editor without OCR. When OCR is requested, identical frames can reuse the last successful recognition result.

| Tool | What it does |
| --- | --- |
| `phone_status` | Returns readiness, current logical dimensions, rotation and the foreground activity when available. |
| `phone_launch` | Opens an installed Android app by package name on the secondary display. |
| `phone_observe` | Returns an observation ID and OCR text regions. Screenshots are opt-in. |
| `phone_tap` | Taps a text region by ID or a point in the observed screen's pixel coordinates. |
| `phone_swipe` | Swipes between two points on the observed screen. |
| `phone_type` | Types single-line text into the focused field. The native editor helper supports Chinese and verifies the result; keyboard mode supports printable ASCII. |
| `phone_key` | Sends `back`, `enter` or `del`. |

### Watch a video on Bilibili

Try asking the assistant to find an uploader, choose a video, and describe two specific player timestamps a minute apart. During the test with `Jerry的杰瑞` and *人工智能视觉_第三轮预赛*, the sampled frames showed people in orange beside a blue-covered table at **01:04**, then a large **5** on the event monitor at **02:09**. The model received these images without an audio track.

## Watch the assistant and troubleshoot

Read `id` from `/run/xfy-phone-use/display.json` in Debian. If the ID is `7`, run the following on your Windows PC with scrcpy 4.1 and an ADB connection.

```powershell
scrcpy --display-id=7 --no-control --no-audio --no-power-on
```

You can close the viewer while the assistant keeps using its display. To release the display itself, run `/opt/xfy-phone-venv/bin/phone-display stop` in a second Debian terminal.

| What you see | What to try |
| --- | --- |
| `Primary foreground could not be verified` | Bring a recognizable app to the foreground on the main display, then check `phone_status` again. |
| An input reports that the view has changed | Call `phone_observe` again and select the target from the new view. |
| The video player switches to landscape | Observe again and use coordinates from the new view. The `vision` extra from the installation steps is needed when the captured image must be resized to match the rotated display. |
| An app launches on the wrong display or changes input focus | Start with Settings to check your setup. Secondary-display support varies by app and Android build, and system input focus can shift briefly. |

To run the source tests, use `python -m unittest discover -s tests -v` from the repository root.
