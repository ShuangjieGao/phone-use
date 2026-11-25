# phone-use

[简体中文](README.md) · [Installation guide](docs/setup.en.md)

**Keep using your Android screen while your AI operates apps on a second display on the same device.** Stay in Stardew Valley while an assistant sends a message in WeChat, works out a seed budget or finds a video on Bilibili.

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/images/stardew-jerry-zh-poster.png">
  <img src="docs/images/stardew-jerry-zh.gif" alt="One Android tablet running Stardew Valley on the main screen while the assistant sends a Chinese draft to Jerry in WeChat on the virtual display. Private parts of older messages are masked.">
</picture>

Here the assistant checks the draft and taps Send. The green outgoing bubble confirms the result. Both displays were recorded together. [Watch the send](docs/images/stardew-jerry-zh.mp4) · [Watch Chinese text entry from an empty field](docs/images/wechat-filehelper-zh.mp4)

### How does the assistant use the phone?

- **Read the screen.** Tesseract runs on the tablet and returns visible Chinese or English text and its position. The assistant can request an image for an unlabeled icon and observe again after a tap or swipe to check the result.
- **Enter text.** With the native input helper enabled, the assistant inserts a whole sentence into the focused editor and reads it back. Sending a message is a separate action.
- **Switch apps.** Open another app on the virtual display to continue the task. In one recording the player walks across the farm while Calculator works out `24 × 20 = 480`. [Watch the calculator](docs/images/stardew-fullscreen.mp4)

In a Bilibili test, the MCP operator found the creator `Jerry的杰瑞`. Xiaofeiyu chose *人工智能视觉_第三轮预赛* from two uploads, the operator opened it, and the model recognized people in orange beside a blue-covered table in one frame and a large **5** on the event monitor in another.

### Ask your AI to install it

Install from source. The tested setup is a **rooted Android tablet with an ARM64 Debian chroot** and an assistant that supports MCP stdio. Give an agent with terminal and device access the repository URL and your goal:

> I want to keep using my Android screen while you operate apps on a virtual display. Check whether my device meets the requirements, follow the installation guide at https://github.com/ShuangjieGao/phone-use, connect it to my MCP client, and verify that you can read the virtual display.

[Installation and troubleshooting](docs/setup.en.md) · [Chinese text input](docs/setup.en.md#type-chinese-text) · [Report a compatible device](https://github.com/ShuangjieGao/phone-use/issues) · [AGPL-3.0-only](LICENSE)
