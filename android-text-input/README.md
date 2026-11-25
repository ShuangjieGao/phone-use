# Local Unicode text helper for Android

This optional APK edits the focused text field on an Android **secondary display**. It has two paths:

- Android 11+ can use short-lived instrumentation and the editable node's public `ACTION_SET_TEXT` action.
- Android 13+ can use `AccessibilityService`'s official input-method connection for editors that do not expose a usable accessibility node. This path requires enabling the system accessibility service **phone-use local text input**. Its manifest grants neither view-tree access nor screenshots. It does not replace your usual keyboard or use the clipboard.

## Build and install

On a Linux development machine, install a JDK and the official Android SDK platform and build-tools. The script defaults to SDK Platform 35. To select a different installed platform with API **33 or later**, set `ANDROID_JAR` to its `android.jar`.

```sh
export ANDROID_HOME="$HOME/Android/Sdk"
sh android-text-input/build-apk.sh /tmp/phone-use-text-input.apk
adb install -r /tmp/phone-use-text-input.apk
```

The build signs the helper with a dedicated, local development key under `~/.android/phone-use-local-input.keystore`. Keep that key locally if you want to update the APK in place. Each developer can build and sign their own helper.

Set `PHONE_USE_TEXT_HELPER=1` in the environment of your `phone-use` MCP server. Focus the field on the virtual display and use `phone_observe` with the `editor` option before requesting text input. Both paths reject password inputs and keep the text inside a short-lived app-private request. A verified result means that the same editor returned the expected text after input.

For WeChat on Android 13+, open **Settings → Accessibility → Downloaded apps → phone-use local text input** and enable the service. The exact settings labels vary by device. Tap the text field *on the virtual display* to establish its input connection. An application window can be visible before its editor has input focus.

Without the service enabled, the device must expose a visible editable node. Instrumentation uses `getUiAutomation(FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES)` and exits after the request. With the service enabled, the host additionally checks the current Android IME client/display and app package; the service binds the write to a single editor session, its content and selection. This uses `commitText` followed by `getSurroundingText`. If readback is inconclusive, inspect the screen before trying another input.

To remove the optional connection service, switch it off in Android's Accessibility settings. To remove the entire helper:

```sh
adb uninstall io.github.phoneuse.textinput
```
