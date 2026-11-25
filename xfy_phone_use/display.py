"""Own one scrcpy 4.1 virtual display and serve its latest PNG to phone-use."""

import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import secrets
import signal
import socket
import struct
import subprocess
import sys
import threading
import time


PNG = b'\x89PNG\r\n\x1a\n'
HEADER = struct.Struct('>IQQ')
MAX_FRAME = 32 * 1024 * 1024
ENTER = ['/usr/bin/nsenter', '-t', '1', '-m', '-r', '-w']


def android(*args):
    command, *rest = args
    result = subprocess.run([*ENTER, '--', '/system/bin/' + command, *rest],
                            env={**os.environ, 'PATH': '/system/bin:/system/xbin:/vendor/bin:/product/bin'},
                            capture_output=True, check=True, timeout=8)
    return result.stdout.decode(errors='replace')


def displays():
    return {int(i): desc for i, desc in re.findall(
        r'^\s*Display id (\d+):([^\n]+)', android('cmd', 'display', 'get-displays'), re.M)}


def new_display(before, now, width, height):
    candidates = {i: desc for i, desc in now.items() if i > 0 and i not in before and 'scrcpy' in desc.lower()}
    if len(candidates) > 1:
        raise RuntimeError('More than one new scrcpy display appeared')
    if candidates:
        did, desc = next(iter(candidates.items()))
        if not re.search(rf'\breal\s+{width}\s*x\s*{height}\b', desc):
            raise RuntimeError('New display geometry does not match the requested geometry')
        return did
    return None


def foregrounds():
    result = {}
    for block in re.split(r'(?=^[ \t]*Display: mDisplayId=)', android('dumpsys', 'window', 'displays'), flags=re.M):
        did = re.match(r'^[ \t]*Display: mDisplayId=(\d+)', block)
        if did:
            focused = re.search(r'mCurrentFocus=Window\{[^\n}]*?\bu\d+ ([\w.$/]+)', block)
            app = re.search(r'mFocusedApp=ActivityRecord\{[^\n}]*?\bu\d+ ([\w.$/]+)', block)
            result[int(did[1])] = focused[1] if focused else (app[1] if app else '')
    return result


def server_options(scid, options):
    return ['4.1', f'scid={scid}', 'log_level=info', 'tunnel_forward=true', 'audio=false',
            'control=false', 'raw_stream=true', 'cleanup=false', 'power_on=false',
            f'new_display={options.width}x{options.height}/{options.density}',
            'vd_system_decorations=false', 'vd_destroy_content=true', 'video_codec=h264',
            'video_bit_rate=6000000', 'max_fps=5']


def start_android(scid, options):
    # Both are init-managed processes. zygote64 also exists with USB debugging disabled.
    try:
        pids = android('pidof', 'adbd').split()
    except subprocess.CalledProcessError:
        pids = []
    if not pids:
        pids = android('pidof', 'zygote64').split()
    if not pids:
        raise RuntimeError('Could not identify an Android ART environment')
    pid = pids[0]
    inherited = Path('/proc', pid, 'environ').read_bytes().split(b'\0')
    env = dict(row.split(b'=', 1) for row in inherited if b'=' in row)
    env[b'PATH'] = b'/system/bin:/system/xbin:/vendor/bin:/product/bin'
    env[b'CLASSPATH'] = os.fsencode(options.server_jar)
    argv = [*ENTER, '--setgid=2000', '--setuid=2000', '--no-fork', '--',
            '/system/bin/app_process', '/', 'com.genymobile.scrcpy.Server', *server_options(scid, options)]
    return subprocess.Popen(argv, env=env, stdin=subprocess.DEVNULL, stdout=sys.stderr)


def connect_video(scid, server, done):
    deadline = time.monotonic() + 12
    while not done.is_set() and time.monotonic() < deadline:
        if server is not None and server.poll() is not None:
            raise RuntimeError(f'Android scrcpy process exited with status {server.returncode}')
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            client.connect('\0scrcpy_' + scid)
            return client
        except (FileNotFoundError, ConnectionRefusedError):
            client.close()
            done.wait(.1)
        except BaseException:
            client.close()
            raise
    raise RuntimeError('Could not connect to the named Android scrcpy socket')


def read_png(stream):
    sig = stream.read(8)
    if not sig:
        return None
    if sig != PNG:
        raise ValueError('Bad PNG signature from ffmpeg')
    chunks, size = [sig], len(sig)
    while True:
        header = stream.read(8)
        if len(header) != 8:
            raise ValueError('Incomplete PNG chunk header')
        count = struct.unpack('>I', header[:4])[0]
        size += 12 + count
        if size > MAX_FRAME:
            raise ValueError('PNG exceeds FRAME limit')
        payload = stream.read(count + 4)
        if len(payload) != count + 4:
            raise ValueError('Incomplete PNG chunk')
        chunks.extend((header, payload))
        if header[4:] == b'IEND':
            return b''.join(chunks)


class Frames:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest = (0, 0, b'')
        self.error = None

    def decode(self, stream):
        try:
            while (png := read_png(stream)) is not None:
                with self.lock:
                    self.latest = (self.latest[0] + 1, time.time_ns() // 1000000, png)
            self.error = 'Decoder reached end of video'
        except (OSError, ValueError) as exc:
            self.error = str(exc)

    def reply(self, conn):
        conn.settimeout(2)
        with conn.makefile('rb') as request:
            if request.read(5) != b'FRAME':
                return
        with self.lock:
            seq, timestamp, png = self.latest
        conn.sendall(HEADER.pack(len(png), seq, timestamp))
        conn.sendall(png)


def terminate(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def serve(options, root, done):
    stop_file = root / 'stop'
    state = root / 'display.json'
    sockfile = root / 'screen.sock'
    for path in (state, sockfile, stop_file):
        path.unlink(missing_ok=True)
    initial = displays()
    primary = foregrounds().get(0)
    if not primary:
        raise RuntimeError('Could not identify the primary foreground window')
    if primary.partition('/')[0] == options.package:
        raise RuntimeError('The requested app is currently in use on the primary display')
    scid = options.external_scid or f'{secrets.randbits(31):08x}'
    frames = Frames()
    with contextlib.ExitStack() as stack:
        stack.callback(state.unlink, missing_ok=True)
        stack.callback(stop_file.unlink, missing_ok=True)
        proc = None
        if not options.external_scid:
            proc = start_android(scid, options)
            stack.callback(terminate, proc)
        video = stack.enter_context(connect_video(scid, proc, done))
        decoder = subprocess.Popen([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-probesize', '65536', '-analyzeduration', '0',
            '-threads', '1', '-flags', 'low_delay', '-f', 'h264', '-i', 'pipe:0', '-an',
            '-fps_mode', 'passthrough', '-threads', '1', '-c:v', 'png', '-compression_level', '1',
            '-f', 'image2pipe', 'pipe:1'], stdin=video, stdout=subprocess.PIPE)
        stack.callback(terminate, decoder)
        reader = threading.Thread(target=frames.decode, args=(decoder.stdout,), daemon=True)
        reader.start()
        deadline = time.monotonic() + 15
        while not done.is_set() and not stop_file.exists() and time.monotonic() < deadline:
            did = new_display(initial, displays(), options.width, options.height)
            if did is not None:
                break
            done.wait(.12)
        else:
            raise RuntimeError('No new scrcpy secondary display appeared')
        if foregrounds().get(0) != primary:
            raise RuntimeError('Primary foreground changed during display creation')
        launcher = ['-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER']
        resolved = android('cmd', 'package', 'resolve-activity', '--brief', '--user', '0',
                           *launcher, '-p', options.package).splitlines()
        component = resolved[-1].strip() if resolved else ''
        if not re.fullmatch(re.escape(options.package) + r'/[\w.$]+', component, re.ASCII):
            raise RuntimeError('Android could not resolve a launcher for the requested package')
        output = android('am', 'start', '--user', '0', '--display', str(did), *launcher, '-n', component)
        if re.search(r'Error|Exception|Permission Denial|unable to resolve', output, re.I):
            raise RuntimeError('Android rejected the launch request')
        listener = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
        listener.bind(str(sockfile))
        stack.callback(sockfile.unlink, missing_ok=True)
        sockfile.chmod(0o600)
        listener.listen(4)
        listener.settimeout(.4)
        deadline = time.monotonic() + 20
        while not done.is_set() and not stop_file.exists() and time.monotonic() < deadline:
            if frames.error:
                raise RuntimeError(frames.error)
            windows = foregrounds()
            if windows.get(0) != primary:
                raise RuntimeError('Primary foreground changed during app launch')
            if windows.get(did, '').partition('/')[0] == options.package and frames.latest[0]:
                break
            done.wait(.18)
        else:
            raise RuntimeError('The secondary app or its first frame did not become ready')
        png = frames.latest[2]
        if png[12:16] != b'IHDR' or struct.unpack('>II', png[16:24]) != (options.width, options.height):
            raise RuntimeError('The decoded frame dimensions differ from the display')
        metadata = {'id': did, 'width': options.width, 'height': options.height, 'density': options.density,
                    'initial_package': options.package}
        temporary = state.with_suffix('.tmp')
        temporary.write_text(json.dumps(metadata), encoding='utf-8')
        temporary.replace(state)
        print(json.dumps(metadata), flush=True)
        while not done.is_set() and not stop_file.exists():
            if frames.error:
                raise RuntimeError(frames.error)
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            with connection:
                try:
                    frames.reply(connection)
                except OSError:
                    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('run', 'stop'))
    parser.add_argument('--server-jar', help='Path to scrcpy-server 4.1 on the Android filesystem.')
    parser.add_argument('--external-scid', help='Connect to an already-started, unconnected raw scrcpy 4.1 server.')
    parser.add_argument('--width', type=int, default=1080)
    parser.add_argument('--height', type=int, default=1920)
    parser.add_argument('--density', type=int, default=420)
    parser.add_argument('--package', help='Android application package to open on the new display (required for run).')
    options = parser.parse_args()
    root = Path(os.environ.get('XFY_PHONE_RUNTIME', '/run/xfy-phone-use'))
    if options.command == 'stop':
        if not root.is_dir():
            return
        (root / 'stop').touch(mode=0o600)
        return
    if bool(options.server_jar) == bool(options.external_scid):
        parser.error('supply exactly one of --server-jar and --external-scid')
    if not options.package:
        parser.error('run requires --package')
    if options.external_scid and not re.fullmatch('[0-7][0-9a-f]{7}', options.external_scid):
        parser.error('--external-scid must be a lowercase eight-digit 31-bit hex ID')
    if not re.fullmatch(r'[a-zA-Z]\w*(?:\.[a-zA-Z]\w*)+', options.package, re.ASCII):
        parser.error('expected an Android package name')
    if min(options.width, options.height, options.density) <= 0 or options.width % 2 or options.height % 2:
        parser.error('dimensions must be positive even numbers and density must be positive')
    import fcntl
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / 'service.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('A display service already owns this runtime directory')
        done = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: done.set())
        serve(options, root, done)


def cli():
    try:
        main()
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        sys.exit(str(exc))


if __name__ == '__main__':
    cli()
