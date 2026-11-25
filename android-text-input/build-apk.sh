#!/bin/sh
# Build a locally signed ADB instrumentation helper. Run on a Linux build host.
set -eu
umask 077
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
out=${1:-"$here/phone-use-text-input.apk"}
mkdir -p "$(dirname -- "$out")"
out=$(CDPATH= cd -- "$(dirname -- "$out")" && pwd)/$(basename -- "$out")
if [ -n "${ANDROID_HOME:-}" ]; then
    tools=$(find "$ANDROID_HOME/build-tools" -mindepth 1 -maxdepth 1 -type d | sort -V | tail -n 1)
    PATH="$tools:$PATH"
fi
platform=${ANDROID_JAR:-"${ANDROID_HOME:?set ANDROID_HOME or ANDROID_JAR}/platforms/android-35/android.jar"}
[ -r "$platform" ] || { echo "Android platform android.jar not found" >&2; exit 1; }
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/classes" "$work/dex"
javac -encoding UTF-8 -source 8 -target 8 -bootclasspath "$platform" -d "$work/classes" \
    "$here"/src/io/github/phoneuse/textinput/*.java
jar cf "$work/classes.jar" -C "$work/classes" .
if command -v d8 >/dev/null 2>&1; then
    d8 --min-api 30 --lib "$platform" --output "$work/dex" "$work/classes.jar"
else
    java -cp "${DX_JAR:?d8 not found; set DX_JAR to an Android dx.jar or install SDK build-tools}" \
         com.android.dx.command.Main --dex --output="$work/dex/classes.dex" "$work/classes.jar"
fi
aapt package -f -M "$here/AndroidManifest.xml" -S "$here/res" -I "$platform" -F "$work/unsigned.apk"
(cd "$work/dex" && aapt add "$work/unsigned.apk" classes.dex >/dev/null)
zipalign -f 4 "$work/unsigned.apk" "$work/aligned.apk"
key=${PHONE_USE_TEXT_KEYSTORE:-"$HOME/.android/phone-use-local-input.keystore"}
if [ ! -e "$key" ]; then
    mkdir -p "$(dirname -- "$key")"
    keytool -genkeypair -keystore "$key" -storepass android -keypass android -alias phoneuse \
        -keyalg RSA -keysize 3072 -validity 3650 -dname 'CN=phone-use local development'
fi
apksigner sign --ks "$key" --ks-key-alias phoneuse --ks-pass pass:android --key-pass pass:android \
    --out "$work/signed.apk" "$work/aligned.apk"
apksigner verify "$work/signed.apk"
cp "$work/signed.apk" "$out"
printf 'Built %s\n' "$out"
