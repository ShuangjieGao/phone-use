"""Operate an existing Android secondary display through its scrcpy FRAME socket."""

import csv
import io
import json
import os
from pathlib import Path
import re
import secrets
import socket
import struct
import subprocess
import time
from dataclasses import dataclass

from .display import foreground_activity, window_activities as _windows


class PhoneError(RuntimeError):
    """An operation failed or could not be verified on the secondary display."""


@dataclass(frozen=True)
class Screen:
    display: int
    width: int
    height: int
    socket_inode: int
    primary: str
    activity: str


def _android(*args):
    program, *rest = args
    try:
        result = subprocess.run(
            ['/usr/bin/nsenter', '-t', '1', '-m', '-r', '-w', '--',
             '/system/bin/' + program, *rest],
            env={**os.environ, 'PATH': '/system/bin:/system/xbin:/vendor/bin:/product/bin'},
            capture_output=True, timeout=10, check=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PhoneError(f'Android {program} failed; check the tablet bridge') from exc
    return result.stdout.decode('utf-8', errors='replace')


def _frame(path, screen):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(4)
            sock.connect(str(path))
            sock.sendall(b'FRAME')
            with sock.makefile('rb') as stream:
                header = stream.read(20)
                if len(header) != 20:
                    raise PhoneError('Incomplete secondary frame header')
                size, seq, captured_ms = struct.unpack('>IQQ', header)
                if not (0 < size <= 32 * 1024 * 1024 and seq and captured_ms):
                    raise PhoneError('No secondary frame available yet')
                png = stream.read(size)
    except OSError as exc:
        raise PhoneError('Secondary frame socket unavailable') from exc
    if (len(png) != size or len(png) < 33 or png[:8] != b'\x89PNG\r\n\x1a\n'
            or png[12:16] != b'IHDR' or struct.unpack('>II', png[16:24]) != (screen.width, screen.height)
            or png[-8:-4] != b'IEND'):
        raise PhoneError('Invalid PNG or secondary display dimensions changed')
    return seq, captured_ms, png


def _ocr(png, width, height):
    try:
        proc = subprocess.run(
            ['tesseract', 'stdin', 'stdout', '-l', 'chi_sim+eng', '--psm', '11', 'tsv'],
            input=png, capture_output=True, timeout=12, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return [], 'OCR unavailable; use observe with screenshot=true'
    lines = {}
    rows = csv.DictReader(io.StringIO(proc.stdout.decode('utf-8', errors='replace')),
                          delimiter='\t', quoting=csv.QUOTE_NONE)
    for row in rows:
        word = (row.get('text') or '').strip()
        if row.get('level') != '5' or not word:
            continue
        x, y, w, h = (int(row[k]) for k in ('left', 'top', 'width', 'height'))
        if not (0 <= x < x+w <= width and 0 <= y < y+h <= height):
            continue
        key = tuple(row[k] for k in ('block_num', 'par_num', 'line_num'))
        lines.setdefault(key, []).append((word, x, y, x+w, y+h))
    elements = []
    for words in lines.values():
        bounds = [min(w[1] for w in words), min(w[2] for w in words),
                  max(w[3] for w in words), max(w[4] for w in words)]
        elements.append({'id': f'e{len(elements)+1}', 'text': ' '.join(w[0] for w in words),
                         'bounds': bounds})
    return elements, None


def _same_tap_target(before, after, bounds):
    """Permit an unrelated animation only when the target and page layout still match."""
    try:
        from PIL import Image, ImageChops, ImageFilter
        a = Image.open(io.BytesIO(before)).convert('RGB')
        b = Image.open(io.BytesIO(after)).convert('RGB')
        if a.size != b.size:
            return False
        difference = ImageChops.difference(a, b)
        channels = difference.split()
        delta = ImageChops.lighter(channels[0], ImageChops.lighter(channels[1], channels[2]))
        # Check both the tap neighbourhood and the overall page. Some animation
        # outside the tap is fine; a scene transition or changed label is not.
        patch = delta.crop(bounds).filter(ImageFilter.BoxBlur(1))
        if sum(patch.histogram()[24:]) > 8:
            return False
        coarse = delta.resize((18,32), Image.Resampling.BOX)
        return sum(coarse.histogram()[32:]) <= 12
    except (ImportError, OSError, ValueError):
        return False


class PhoneDevice:
    def __init__(self):
        self.runtime = Path(os.environ.get('XFY_PHONE_RUNTIME', '/run/xfy-phone-use'))
        self.observation = None
        self.primary = None
        self.input_frame = None

    def _screen(self):
        path = self.runtime / 'screen.sock'
        try:
            config = json.loads((self.runtime / 'display.json').read_bytes())
            display, width, height = (config[k] for k in ('id', 'width', 'height'))
            if not all(type(i) is int and i > 0 for i in (display, width, height)):
                raise PhoneError('A positive secondary display ID and dimensions are required')
            if not path.is_socket():
                raise PhoneError('No active secondary display socket')
            inode = path.stat().st_ino
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise PhoneError('Secondary display runtime unavailable or malformed') from exc
        displays = _android('cmd', 'display', 'get-displays')
        entry = re.search(rf'^\s*Display id {display}:([^\n]+)', displays, re.M)
        if not entry or 'scrcpy' not in entry[1].lower():
            raise PhoneError('Selected display is no longer a scrcpy display')
        windows = _windows(_android('dumpsys', 'window', 'displays'))
        primary = foreground_activity(*windows.get(0, ('', '')), primary=True)
        if not primary:
            raise PhoneError('Primary foreground could not be verified')
        if self.primary and primary != self.primary:
            raise PhoneError('Primary foreground changed; stopped secondary control')
        self.primary = primary
        if display not in windows:
            raise PhoneError('Secondary window disappeared')
        activity = foreground_activity(*windows[display], primary=False)
        return Screen(display, width, height, inode, primary, activity)

    def status(self):
        try:
            screen = self._screen()
        except PhoneError as exc:
            return {'ready': False, 'reason': str(exc)}
        return {'ready': True, 'display_id': screen.display, 'width': screen.width,
                'height': screen.height, 'activity': screen.activity, 'observation_source': 'ocr'}

    def _capture(self, screen):
        seq, timestamp, png = _frame(self.runtime / 'screen.sock', screen)
        if self._screen() != screen:
            raise PhoneError('Display or foreground changed during capture; observe again')
        return seq, timestamp, png

    def observe(self, screenshot=False, elements=True):
        self.observation = None
        screen = self._screen()
        deadline = time.monotonic() + 2
        while True:
            seq, timestamp, png = self._capture(screen)
            if not self.input_frame or self.input_frame[0] != screen.socket_inode or timestamp >= self.input_frame[1]:
                break
            if time.monotonic() >= deadline:
                raise PhoneError('Waiting for a frame after input; observe again')
            time.sleep(.1)
        found, warning = _ocr(png, screen.width, screen.height) if elements else ([], None)
        observation_id = secrets.token_hex(12)
        self.observation = (observation_id, screen, png, found)
        result = {'observation_id': observation_id, 'display_id': screen.display,
                  'width': screen.width, 'height': screen.height, 'activity': screen.activity,
                  'source': 'ocr' if elements else 'frame', 'elements': found,
                  'frame_sequence': seq, 'captured_ms': timestamp}
        if warning:
            result['warning'] = warning
        if screenshot:
            result['image_png'] = png
        return result

    def launch(self, package):
        if not isinstance(package, str) or not re.fullmatch(r'[A-Za-z]\w*(?:\.[A-Za-z]\w*)+', package, re.ASCII):
            raise PhoneError('Expected an Android package name')
        screen = self._screen()
        if screen.primary.split('/')[0] == package:
            raise PhoneError('This application is in use on the primary display')
        if screen.activity.split('/')[0] == package:
            return {'launched': True, 'already_open': True, 'display_id': screen.display, 'activity': screen.activity}
        launcher = ['-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER']
        resolved = _android('cmd', 'package', 'resolve-activity', '--brief', '--user', '0',
                            *launcher, '-p', package).splitlines()
        component = resolved[-1].strip() if resolved else ''
        if not re.fullmatch(re.escape(package) + r'/[\w.$]+', component, re.ASCII):
            raise PhoneError('No launcher activity found for the requested package')
        self.observation = None
        requested_at_ms = int(time.time()*1000)
        reply = _android('am', 'start', '--user', '0', '--display', str(screen.display),
                         *launcher, '-n', component)
        if 'Error' in reply or 'Exception' in reply:
            raise PhoneError('Android rejected launch on the secondary display')
        until = time.monotonic() + 4
        while True:
            after = self._screen()
            if after.socket_inode != screen.socket_inode or after.display != screen.display:
                raise PhoneError('Secondary display was replaced during launch')
            if after.activity.split('/')[0] == package:
                self.input_frame = (screen.socket_inode, requested_at_ms)
                return {'launched': True, 'display_id': after.display, 'activity': after.activity}
            if time.monotonic() >= until:
                raise PhoneError('Launch unconfirmed on the secondary display; observe before retrying')
            time.sleep(.15)

    def act(self, action, observation_id, *, x=None, y=None, to_x=None, to_y=None,
            text=None, key=None, element=None):
        old = self.observation
        if not old or observation_id != old[0]:
            raise PhoneError('Unknown or expired observation; observe again')
        _, screen, old_png, elements = old
        if not screen.activity:
            raise PhoneError('No focused secondary application')
        def point(px, py):
            if not (type(px) is int and type(py) is int and 0 <= px < screen.width and 0 <= py < screen.height):
                raise PhoneError('Coordinates outside the observed secondary display')
            return [str(px), str(py)]
        if action == 'tap':
            target_bounds = None
            if element is not None:
                if x is not None or y is not None:
                    raise PhoneError('Specify an element or coordinates')
                bounds = next((item['bounds'] for item in elements if item['id'] == element), None)
                if bounds is None:
                    raise PhoneError('Unknown OCR element in this observation')
                left, top, right, bottom = bounds
                x, y = (left+right)//2, (top+bottom)//2
                target_bounds = (left, top, right, bottom)
            args = ['tap', *point(x, y)]
            if target_bounds is None:
                radius = max(24, min(screen.width, screen.height)//16)
                target_bounds = (x-radius, y-radius, x+radius+1, y+radius+1)
            left, top, right, bottom = target_bounds
            target_bounds = (max(0,left-12),max(0,top-12),min(screen.width,right+12),min(screen.height,bottom+12))
        elif action == 'swipe':
            args = ['swipe', *point(x, y), *point(to_x, to_y), '320']
        elif action == 'text':
            if not isinstance(text, str) or not re.fullmatch(r'[ -~]+', text) or '%s' in text:
                raise PhoneError('Android input text supports printable ASCII here; literal %s and Unicode are unsupported')
            args = ['text', text.replace(' ', '%s')]
        elif action == 'key':
            keys = {'back': 'KEYCODE_BACK', 'enter': 'KEYCODE_ENTER', 'del': 'KEYCODE_DEL'}
            if key not in keys:
                raise PhoneError('Supported keys: back, enter, del')
            args = ['keyevent', keys[key]]
        else:
            raise PhoneError('Unknown phone action')
        if self._screen() != screen:
            self.observation = None
            raise PhoneError('Foreground or display changed; observe again')
        _, _, png = self._capture(screen)
        self.observation = None
        if png != old_png and (action != 'tap' or not _same_tap_target(old_png, png, target_bounds)):
            raise PhoneError('Screen changed; observe again before acting')
        self.input_frame = (screen.socket_inode, int(time.time()*1000))
        _android('input', '-d', str(screen.display), *args)
        after = self._screen()
        if (after.display, after.socket_inode) != (screen.display, screen.socket_inode):
            raise PhoneError('Display changed after input; outcome unconfirmed')
        return {'submitted': True, 'display_id': screen.display, 'action': action,
                'activity': after.activity, 'effect': 'unverified'}

    def close(self):
        self.observation = None
        self.input_frame = None
        self.primary = None
