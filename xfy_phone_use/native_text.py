"""Local Android instrumentation transport. Request bodies never appear in process arguments."""

import json
import os
import re
import secrets
import subprocess
import time
from contextlib import contextmanager, ExitStack, suppress
from pathlib import Path


PACKAGE = 'io.github.phoneuse.textinput'
COMPONENT = PACKAGE + '/.TextInput'
ENTER = ['/usr/bin/nsenter', '-t', '1', '-m', '-r', '-w', '--']
REPLY_PREFIX = 'INSTRUMENTATION_RESULT: phone_use='
REASONS = {
    'bad_request', 'automation_unavailable', 'display_missing', 'editor_unavailable', 'ambiguous_editor',
    'password_field', 'unknown_selection', 'target_changed', 'dispatch_uncertain', 'readback_failed',
    'helper_unavailable', 'helper_no_reply',
    'input_session_missing', 'input_package_mismatch', 'surrounding_text_unavailable',
    'unsupported_editor', 'ime_target_changed', 'transport_changed', 'request_expired',
}


def _android_environment():
    path = b'/system/bin:/system/xbin:/vendor/bin:/product/bin'
    env = {b'PATH': path}
    for daemon in ('adbd', 'zygote64'):
        pids = subprocess.run([*ENTER, '/system/bin/pidof', daemon], env=env, capture_output=True,
                              timeout=3).stdout.split()
        if pids:
            for item in Path('/proc', pids[0].decode(), 'environ').read_bytes().split(b'\0'):
                key, sep, value = item.partition(b'=')
                if sep and (key.startswith((b'ANDROID_', b'APEX_')) or key.endswith(b'CLASSPATH')):
                    env[key] = value
            break
    return env


@contextmanager
def _mailbox(request, service=False):
    """Stage one single-use file in our own app's internal storage, even without ADB."""
    root = '/proc/1/root/data/user/0/' + PACKAGE
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    name = secrets.token_hex(16)
    filename = name + ('.tmp' if service else '.json')
    data = json.dumps(request, ensure_ascii=False).encode('utf-8')
    with ExitStack() as cleanup:
        app = os.open(root, flags)
        cleanup.callback(os.close, app)
        owner = os.fstat(app)
        if not 10000 <= owner.st_uid < 100000:
            raise PermissionError('Unexpected Android application owner')
        try:
            files = os.open('files', flags, dir_fd=app)
        except FileNotFoundError:
            os.mkdir('files', mode=0o700, dir_fd=app)
            files = os.open('files', flags, dir_fd=app)
            try:
                os.fchown(files, owner.st_uid, owner.st_gid)
                app_label = os.getxattr(app, 'security.selinux')
                if os.getxattr(files, 'security.selinux') != app_label:
                    os.setxattr(files, 'security.selinux', app_label)
            except OSError:
                os.close(files)
                raise
        cleanup.callback(os.close, files)
        if os.fstat(files).st_uid != owner.st_uid:
            raise PermissionError('Unexpected Android mailbox owner')
        fd = os.open(filename, os.O_EXCL | os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW,
                     0o600, dir_fd=files)
        def remove():
            for suffix in ('.tmp', '.ime', '.out') if service else ('.json',):
                with suppress(FileNotFoundError):
                    os.unlink(name + suffix, dir_fd=files)
        cleanup.callback(remove)
        with os.fdopen(fd, 'wb') as output:
            os.fchown(output.fileno(), owner.st_uid, owner.st_gid)
            # New app-private files must carry this package's SELinux category.
            label = os.getxattr(files, 'security.selinux')
            if os.getxattr(output.fileno(), 'security.selinux') != label:
                os.setxattr(output.fileno(), 'security.selinux', label)
            output.write(data)
            if service:
                output.flush()
                os.fsync(output.fileno())
        yield files, name


def _invoke(nonce, env):
    return subprocess.run(
        [*ENTER, '/system/bin/am', 'instrument', '--user', '0', '-w', '-r', '-e', 'request', nonce, COMPONENT],
        env=env, stdin=subprocess.DEVNULL, capture_output=True, timeout=15,
    )


def _system_output(env, command, *args):
    proc = subprocess.run([*ENTER, '/system/bin/' + command, *args], env=env, capture_output=True,
                          timeout=5, check=True, stdin=subprocess.DEVNULL)
    return proc.stdout.decode('utf-8', errors='replace')


def _service_enabled(env):
    value = _system_output(env, 'cmd', 'settings', '--user', '0', 'get', 'secure',
                           'enabled_accessibility_services').strip()
    if value == 'null':
        return False
    enabled = [name for name in value.split(':') if name]
    if any(not re.fullmatch(r'[\w.$]+/[\w.$]+', name) for name in enabled):
        raise ValueError('Unknown accessibility-services response')
    return any(name in enabled for name in (PACKAGE + '/.EditorService', PACKAGE + '/' + PACKAGE + '.EditorService'))


def _client_target(dump, display, uid, package):
    # A stale EditorInfo can survive a focus transfer on MIUI. Require both live
    # ClientState records to refer to the same object, process, UID and display.
    client = re.search(r'^[ \t]*mCurClient=ClientState\{([^}\n]+)\}', dump, re.M)
    focused = re.search(r'^[ \t]*mCurFocusedWindow=(?!null)[^\n]*?\bclient=ClientState\{([^}\n]+)\}', dump, re.M)
    def identity(match):
        if not match:
            return None
        record = match[1]
        fields = dict(re.findall(r'\b(uid|pid|displayId)=([0-9]+)\b', record))
        if set(fields) != {'uid', 'pid', 'displayId'} or int(fields['pid']) <= 0:
            return None
        return record.split()[0], int(fields['uid']), int(fields['pid']), int(fields['displayId'])
    live = identity(client)
    if not (display > 0 and live and live == identity(focused) and live[1] == uid and live[3] == display):
        return False
    attribute = re.search(r'^(?P<indent>[ \t]*)m(?:CurrentTextBoxAttribute|CurrentEditorInfo|CurAttribute):[ \t]*$', dump, re.M)
    if attribute:
        for line in dump[attribute.end():].splitlines()[1:9]:
            if not line.startswith(attribute['indent']) or len(line) - len(line.lstrip()) <= len(attribute['indent']):
                break
            field = re.match(r'\s*packageName=([^\s]+)', line)
            if field:
                return field[1] == package
    return False


def _ime_matches(env, request, uid):
    return _client_target(_system_output(env, 'dumpsys', 'input_method'), request['display'], uid, request['package'])


def _service_exchange(request, env):
    pkg = request['package']
    listing = _system_output(env, 'cmd', 'package', 'list', 'packages', '-U', '--user', '0', pkg)
    match = re.search(r'^package:' + re.escape(pkg) + r'[ \t]+uid:(\d+)[ \t]*$', listing, re.M)
    if not match or not _ime_matches(env, request, int(match[1])):
        return {'status': 'not_applied', 'reason': 'ime_target_changed'}
    uid = int(match[1])
    attempted = False
    try:
        with _mailbox({**request, 'expires_ms': time.time_ns() // 1_000_000 + 4000}, service=True) as (files, nonce):
            os.rename(nonce + '.tmp', nonce + '.ime', src_dir_fd=files, dst_dir_fd=files)
            attempted = request['op'] == 'insert'
            until = time.monotonic() + 6
            while time.monotonic() < until:
                try:
                    fd = os.open(nonce + '.out', os.O_RDONLY | os.O_NOFOLLOW, dir_fd=files)
                except FileNotFoundError:
                    time.sleep(.04)
                    continue
                with os.fdopen(fd, 'rb') as inp:
                    if os.fstat(inp.fileno()).st_uid != os.fstat(files).st_uid:
                        break
                    reply = json.loads(inp.read(4097))
                if not isinstance(reply, dict) or reply.get('status') not in ('ready','verified','not_applied','unconfirmed'):
                    break
                if reply['status'] in ('ready','verified') and not _ime_matches(env, request, uid):
                    break
                return {**reply, 'transport': 'input_connection'}
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return {'status': 'unconfirmed' if attempted else 'not_applied', 'reason': 'helper_no_reply'}


def _exchange(request, expected_transport=None):
    attempted = False
    try:
        env = _android_environment()
        resident = _service_enabled(env)
        transport = 'input_connection' if resident else 'instrumentation'
        if expected_transport is not None and expected_transport != transport:
            return {'status': 'not_applied', 'reason': 'transport_changed'}
        if resident:
            return _service_exchange(request, env)
        with _mailbox(request) as (_, nonce):
            attempted = request['op'] == 'insert'
            proc = _invoke(nonce, env)
        if not proc.returncode:
            replies = [line[len(REPLY_PREFIX):] for line in proc.stdout.decode('utf-8').splitlines()
                       if line.startswith(REPLY_PREFIX)]
            if len(replies) == 1:
                reply = json.loads(replies[0])
                if isinstance(reply, dict) and reply.get('status') in (
                        'ready', 'verified', 'not_applied', 'unconfirmed'):
                    return {**reply, 'transport': 'instrumentation'}
    except (OSError, subprocess.SubprocessError, ValueError):
        if not attempted:
            return {'status': 'not_applied', 'reason': 'helper_unavailable'}
    return {'status': 'unconfirmed' if attempted else 'not_applied', 'reason': 'helper_no_reply'}


def request_editor(screen, op, *, transport=None, **fields):
    # Android runs one instrumentation at a time for this package. Serialize requests
    # across stdio clients to avoid interrupting a write before its readback.
    request = {'op': op, 'display': screen.display, 'package': screen.activity.partition('/')[0], **fields}
    try:
        import fcntl
        with open('/run/phone-use-text-input.lock', 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            response = _exchange(request, transport)
    except (ImportError, OSError, subprocess.SubprocessError):
        return {'status': 'not_applied', 'reason': 'helper_unavailable'}
    state = response['status']
    if op == 'inspect' and state == 'ready':
        bounds = response.get('bounds')
        token = response.get('token')
        if isinstance(token, str) and re.fullmatch('[0-9a-f]{64}', token):
            if response.get('transport') == 'input_connection':
                return {'status': 'ready', 'token': token, 'transport': 'input_connection'}
            if (isinstance(bounds, list) and len(bounds) == 4 and all(type(i) is int for i in bounds)
                    and 0 <= bounds[0] < bounds[2] <= screen.width and 0 <= bounds[1] < bounds[3] <= screen.height):
                return {'status': 'ready', 'token': token, 'bounds': bounds, 'transport':'instrumentation'}
    if op == 'insert' and state == 'verified':
        return {'status': 'verified'}
    # Ignore unexpected/verbose diagnostics; never forward the Android command output.
    reason = response.get('reason')
    if not isinstance(reason, str) or reason not in REASONS:
        reason = 'editor_unavailable'
    return {'status': 'unconfirmed' if op == 'insert' and state != 'not_applied' else 'not_applied',
            'reason': reason}
