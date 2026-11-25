import struct
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from xfy_phone_use import device


class PhoneDeviceTests(unittest.TestCase):
    def setUp(self):
        self.phone = device.PhoneDevice()
        self.phone.runtime = Path('runtime')
        self.phone.native_text = False
        self.screen = device.Screen(8, 160, 120, 44, 'moon/Game', 'com.qq/.Main')
        self.png = b'frame A'
        self.call = self.start_patch('_android', return_value='')
        self.screen_call = self.start_patch('PhoneDevice._screen', return_value=self.screen)
        self.capture = self.start_patch('PhoneDevice._capture', return_value=(2, 1000, self.png))
        self.start_patch('_ocr', return_value=([{'id':'e1','text':'Hello','bounds':[4,6,16,24]}], None))

    def start_patch(self, name, **kwargs):
        scope = patch('xfy_phone_use.device.' + name, **kwargs)
        self.addCleanup(scope.stop)
        return scope.start()

    def test_only_secondary_input_and_observations_are_single_use(self):
        result = self.phone.observe(screenshot=True)
        self.assertEqual(result['image_png'], self.png)
        outcome = self.phone.act('tap', result['observation_id'], element='e1')
        self.call.assert_called_once_with('input', '-d', '8', 'tap', '10', '15')
        self.assertEqual(outcome['effect'], 'unverified')
        with self.assertRaises(device.PhoneError):
            self.phone.act('tap', result['observation_id'], element='e1')
        self.assertEqual(self.call.call_count, 1)

    def test_pixels_changed(self):
        observation = self.phone.observe()
        self.assertNotIn('image_png', observation)
        self.capture.return_value = (3, 1050, b'frame B')
        with self.assertRaisesRegex(device.PhoneError, 'Screen changed'):
            self.phone.act('key', observation['observation_id'], key='back')
        self.call.assert_not_called()

    def test_observed_taps_use_logical_landscape_axes_and_expire_on_rotation(self):
        landscape = replace(self.screen, width=240, height=120, rotation=1, configured_size=(120,240))
        self.screen_call.return_value = landscape
        view = self.phone.observe(elements=False)
        self.assertEqual((view['width'], view['height'], view['rotation']), (240, 120, 1))
        self.phone.act('tap', view['observation_id'], x=220, y=12)
        self.call.assert_called_once_with('input', '-d','8','tap','220','12')
        self.phone.close()
        view = self.phone.observe(elements=False)
        self.screen_call.return_value = replace(landscape, rotation=3)
        with self.assertRaisesRegex(device.PhoneError, 'Foreground or display changed'):
            self.phone.act('tap', view['observation_id'], x=220, y=12)
        self.assertEqual(self.call.call_count, 1)

    def test_frame_only_observation_skips_ocr(self):
        with patch.object(device, '_ocr') as ocr:
            view = self.phone.observe(elements=False, screenshot=True)
        ocr.assert_not_called()
        self.assertEqual(view['source'], 'frame')
        self.assertEqual(view['elements'], [])
        self.assertEqual(view['image_png'], self.png)

    def test_verified_unicode_uses_bound_editor_without_exposing_native_token(self):
        self.phone.native_text = True
        binding = {'status': 'ready', 'bounds': [8, 61, 120, 82], 'token': 'opaque'}
        request = self.start_patch('request_editor', side_effect=[binding, {'status': 'verified'}])
        view = self.phone.observe(elements=False, editor=True)
        self.assertEqual(view['editor'], {'status': 'ready', 'bounds': binding['bounds']})
        outcome = self.phone.act('text', view['observation_id'], text='买种子 %s 🌱')
        self.assertEqual(outcome['effect'], 'verified')
        self.assertTrue(outcome['readback'])
        self.assertNotIn('买种子', str(outcome))
        request.assert_called_with(self.screen, 'insert', token='opaque', text='买种子 %s 🌱', transport='instrumentation')
        self.call.assert_not_called()
        with self.assertRaisesRegex(device.PhoneError, 'expired'):
            self.phone.act('text', view['observation_id'], text='买种子 %s 🌱')
        self.assertEqual(request.call_count, 2)

    def test_native_input_rechecks_editor_for_existing_observe_callers(self):
        self.phone.native_text = True
        request = self.start_patch('request_editor', side_effect=[
            {'status': 'ready', 'bounds': [8, 61, 120, 82], 'token': 'opaque'},
            {'status': 'verified'}])
        view = self.phone.observe(elements=False)
        request.assert_not_called()
        self.phone.act('text', view['observation_id'], text='hello')
        self.assertEqual([c.args[1] for c in request.call_args_list], ['inspect', 'insert'])
        self.call.assert_not_called()

    def test_changed_frame_requires_editor_binding_and_a_caret_sized_difference(self):
        self.phone.native_text = True
        request = self.start_patch('request_editor', return_value={
            'status': 'ready', 'bounds': [8, 61, 120, 82], 'token': 'opaque'})
        self.capture.return_value = (1, 1000, b'old frame')
        view = self.phone.observe()
        self.capture.return_value = (2, 1001, b'new frame')
        with self.assertRaisesRegex(device.PhoneError, 'Screen changed'):
            self.phone.act('text', view['observation_id'], text='hello')
        request.assert_not_called()
        for same in (False, True):
            with self.subTest(same=same), patch.object(device, '_same_except_caret', return_value=same):
                request.return_value = {'status': 'ready', 'bounds': [8, 61, 120, 82], 'token': 'opaque'}
                self.capture.return_value = (1, 1000, b'old frame')
                view = self.phone.observe(editor=True)
                self.capture.return_value = (2, 1001, b'new frame')
                request.return_value = {'status': 'verified'}
                if same:
                    self.assertTrue(self.phone.act('text', view['observation_id'], text='中文')['readback'])
                else:
                    with self.assertRaisesRegex(device.PhoneError, 'Screen changed'):
                        self.phone.act('text', view['observation_id'], text='中文')
        self.call.assert_not_called()

    def test_native_failure_never_replays_via_key_events(self):
        self.phone.native_text = True
        binding = {'status': 'ready', 'bounds': [8, 61, 120, 82], 'token': 'opaque'}
        for status in ('not_applied', 'unconfirmed'):
            replies = [binding, {'status': status, 'reason': 'target_changed'}]
            with self.subTest(status=status), patch.object(device, 'request_editor', side_effect=replies) as native:
                view = self.phone.observe(editor=True)
                with self.assertRaises(device.TextInputError) as ctx:
                    self.phone.act('text', view['observation_id'], text='hello')
                self.assertEqual(ctx.exception.receipt['effect'], status)
                self.assertEqual(ctx.exception.receipt['retry_safe'], status == 'not_applied')
                self.assertEqual(native.call_count, 2)
        self.call.assert_not_called()

    def test_bound_input_connection_ignores_small_animations_but_rechecks_the_original_token(self):
        self.phone.native_text = True
        binding = {'status': 'ready', 'transport': 'input_connection', 'token': 'observed-editor'}
        native = self.start_patch('request_editor')
        for insertion in ({'status': 'verified'}, {'status': 'not_applied', 'reason': 'target_changed'}):
            with self.subTest(reply=insertion), patch.object(device, '_same_page', return_value=True):
                self.phone.close()
                native.reset_mock()
                native.side_effect = [binding, insertion]
                self.capture.return_value = (1, 1000, b'page before animation')
                view = self.phone.observe(elements=False, editor=True)
                self.capture.return_value = (2, 1001, b'page after animation')
                if insertion['status'] == 'verified':
                    self.assertTrue(self.phone.act('text', view['observation_id'], text='测试')['readback'])
                else:
                    with self.assertRaisesRegex(device.TextInputError, 'target_changed'):
                        self.phone.act('text', view['observation_id'], text='测试')
                native.assert_called_with(self.screen, 'insert', token='observed-editor', text='测试',
                                          transport='input_connection')
                self.assertEqual(native.call_count, 2)
                with self.assertRaisesRegex(device.PhoneError, 'expired'):
                    self.phone.act('text', view['observation_id'], text='测试')
        self.call.assert_not_called()

    def test_bound_input_connection_rejects_scene_or_foreground_change_before_dispatch(self):
        self.phone.native_text = True
        native = self.start_patch('request_editor', return_value={
            'status': 'ready', 'transport': 'input_connection', 'token': 'observed-editor'})
        for changed_window in (False, True):
            with self.subTest(changed_window=changed_window), patch.object(device, '_same_page', return_value=False):
                self.screen_call.return_value = self.screen
                self.capture.return_value = (1, 1000, b'old frame')
                view = self.phone.observe(elements=False, editor=True)
                native.reset_mock()
                if changed_window:
                    self.screen_call.return_value = device.Screen(8, 160, 120, 44, 'moon/Game', 'other/.UI')
                self.capture.return_value = (2, 1001, b'new scene')
                with self.assertRaisesRegex(device.PhoneError, 'Foreground|Screen changed'):
                    self.phone.act('text', view['observation_id'], text='测试')
                native.assert_not_called()
        self.call.assert_not_called()

    def test_observing_password_field_allows_other_actions_but_never_types(self):
        self.phone.native_text = True
        native = self.start_patch('request_editor', return_value={'status': 'not_applied', 'reason':'password_field'})
        view = self.phone.observe(editor=True)
        self.assertEqual(view['editor']['reason'], 'password_field')
        with self.assertRaises(device.TextInputError):
            self.phone.act('text', view['observation_id'], text='hello')
        native.assert_called_once_with(self.screen, 'inspect')
        self.call.assert_not_called()

    def test_changed_window_and_primary_package_do_not_receive_actions(self):
        observation = self.phone.observe()
        self.screen_call.return_value = device.Screen(8, 160, 120, 44, 'moon/Game', 'other/.UI')
        with self.assertRaisesRegex(device.PhoneError, 'Foreground'):
            self.phone.act('key', observation['observation_id'], key='back')
        self.screen_call.return_value = device.Screen(8, 160, 120, 44, 'net.moon/.Game', 'other/.UI')
        with self.assertRaisesRegex(device.PhoneError, 'primary'):
            self.phone.launch('net.moon')
        self.call.assert_not_called()

    def test_ascii_and_coordinates_are_checked_before_input(self):
        token = self.phone.observe()['observation_id']
        for action, fields in [('text', {'text':'中文'}), ('text', {'text':'literal%s'}),
                               ('swipe', dict(x=160, y=0, to_x=0, to_y=119)),
                               ('tap', dict(x=True, y=0)), ('key', {'key':'home'})]:
            with self.subTest(action=action, fields=fields), self.assertRaises(device.PhoneError):
                self.phone.act(action, token, **fields)
        self.call.assert_not_called()
        self.phone.act('text', token, text='hello world')
        self.call.assert_called_once_with('input', '-d', '8', 'text', 'hello%sworld')

    def test_launch_explicit_target_and_confirm_actual_activity(self):
        new = device.Screen(8, 160, 120, 44, 'moon/Game', 'org.example/.Main')
        self.call.side_effect = ['org.example/.Launcher', 'Starting: Intent']
        self.screen_call.side_effect = [self.screen, new]
        result = self.phone.launch('org.example')
        self.assertEqual(result['activity'], 'org.example/.Main')
        self.assertIn('--display', self.call.call_args.args)
        self.assertEqual(self.call.call_args.args[5], '8')
        self.assertEqual(self.call.call_args.args[-2:], ('-n','org.example/.Launcher'))


class TransportTests(unittest.TestCase):
    def test_ffmpeg_quarter_turn_frame_is_resized_upright_or_rejected(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest('Install the vision extra to align legacy rotated PNG frames')
        # A marker in the right half of an upright (but squashed) encoded PNG
        # must still be in the right half, never in the top/bottom as a transpose would do.
        im = Image.new('RGB', (30, 60), 'navy')
        im.paste('red', (15, 0, 30, 30))
        buf = device.io.BytesIO()
        im.save(buf, 'PNG')
        png = buf.getvalue()
        screen = device.Screen(8, 60, 30, 1, 'main/Game', 'view/Video', 1, (30,60))
        payload = struct.pack('>IQQ',len(png),7,5000) + png
        def read(s):
            with patch.object(device.socket, 'AF_UNIX', 1, create=True), patch.object(device.socket, 'socket') as factory:
                factory.return_value.__enter__.return_value.makefile.return_value = device.io.BytesIO(payload)
                return device._frame(Path('screen.sock'),s)
        seq,stamp,fr = read(screen)
        out = Image.open(device.io.BytesIO(fr))
        self.assertEqual((seq,stamp,out.size),(7,5000,(60,30)))
        self.assertEqual(out.getpixel((50,4)), (255,0,0))
        self.assertEqual(out.getpixel((4,4)), (0,0,128))
        self.assertEqual(out.getpixel((50,26)), (0,0,128))
        for unsafe in [replace(screen,rotation=0), replace(screen,configured_size=(15,30)), replace(screen,width=59)]:
            with self.subTest(screen=unsafe), self.assertRaisesRegex(device.PhoneError, 'geometry'):
                read(unsafe)
    def test_caret_may_change_but_screen_elsewhere_must_match(self):
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            self.skipTest('Install vision to compare frames')
        img = Image.new('RGB', (160, 120), 'white')
        def png():
            output = device.io.BytesIO()
            img.save(output, 'PNG')
            return output.getvalue()
        original = png()
        bounds = (10, 80, 115, 112)
        draw = ImageDraw.Draw(img)
        draw.rectangle((24, 92, 25, 105), fill='black')
        self.assertTrue(device._same_except_caret(original, png(), bounds))
        draw.rectangle((33, 94, 37, 101), fill='black')
        self.assertFalse(device._same_except_caret(original, png(), bounds))
        draw.rectangle((35, 7, 45, 19), fill='black')
        self.assertFalse(device._same_except_caret(original, png(), bounds))
    def test_tap_guard_allows_small_remote_animation_and_rejects_changed_target(self):
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            self.skipTest('Install the vision extra to test changing frames')
        def encode(im):
            buf=device.io.BytesIO()
            im.save(buf,'PNG')
            return buf.getvalue()
        image=Image.new('RGB',(200,300),'white')
        before=encode(image)
        painter=ImageDraw.Draw(image)
        painter.rectangle((160,240,170,250),fill='red')
        self.assertTrue(device._same_page(before,encode(image),(24,24,120,120)))
        self.assertTrue(device._same_page(before,encode(image)))
        painter.rectangle((3,5,10,12),fill='blue')
        self.assertFalse(device._same_except_caret(before,encode(image),None))
        self.assertTrue(device._same_page(before,encode(image)))
        painter.rectangle((70,60,79,66),fill='black')
        self.assertFalse(device._same_page(before,encode(image),(24,24,120,120)))
        painter.rectangle((0,0,199,149),fill='black')
        self.assertFalse(device._same_page(before,encode(image)))
    def test_framed_secondary_protocol_and_pixels(self):
        fake_unix = patch.object(device.socket, 'AF_UNIX', 1, create=True)
        fake_unix.start()
        self.addCleanup(fake_unix.stop)
        signature = b'\x89PNG\r\n\x1a\n'
        png = (signature + struct.pack('>I',13) + b'IHDR' + struct.pack('>II', 160,120)
               + bytes(9) + struct.pack('>I',0) + b'IEND' + bytes(4))
        screen = device.Screen(8, 160, 120, 1, 'moon/Game', 'qq/Main')
        for payload, accepted in [(png, True), (png[:18], False), (b'x'*64,False)]:
            with self.subTest(accepted=accepted), patch.object(device.socket, 'socket') as factory:
                sock = factory.return_value.__enter__.return_value
                sock.makefile.return_value = device.io.BytesIO(struct.pack('>IQQ',len(payload),7,5000)+payload)
                if accepted:
                    self.assertEqual(device._frame(Path('screen.sock'), screen), (7,5000,png))
                else:
                    with self.assertRaises(device.PhoneError):
                        device._frame(Path('screen.sock'), screen)
                sock.sendall.assert_called_once_with(b'FRAME')

    def test_display_zero_fails_before_android(self):
        with tempfile.TemporaryDirectory() as root, patch.object(device, '_android') as android:
            phone = device.PhoneDevice()
            phone.runtime = Path(root)
            (phone.runtime/'display.json').write_text('{"id":0,"width":160,"height":120}')
            self.assertFalse(phone.status()['ready'])
            android.assert_not_called()

    def test_runtime_geometry_tracks_android_rotation_and_discards_pre_rotation_observations(self):
        dump = ('Display: mDisplayId=0\n  mCurrentFocus=Window{aa u0 org.game/Game}\n'
                'Display: mDisplayId=8\n  mCurrentFocus=Window{bb u0 org.video/Player}\n')
        with tempfile.TemporaryDirectory() as root, patch.object(Path, 'is_socket', return_value=True), \
                patch.object(device, '_android') as android:
            phone = device.PhoneDevice()
            phone.runtime = Path(root)
            (phone.runtime / 'display.json').write_text('{"id":8,"width":120,"height":240}')
            (phone.runtime / 'screen.sock').touch()
            def status(info):
                android.side_effect = ['Display id 8: DisplayInfo{"scrcpy", ' + info + '}', dump]
                return phone.status()
            self.assertTrue(status('real 120 x 240, rotation 0')['ready'])
            phone.observation = 'old coordinates'
            landscape = status('real 240 x 120, rotation 1')
            self.assertEqual((landscape['width'],landscape['height'],landscape['rotation']), (240,120,1))
            self.assertIsNone(phone.observation)
            self.assertIsNotNone(phone.input_frame)
            # Even a full turn back to the original size must not revive old coordinates.
            phone.observation = 'old landscape'
            self.assertTrue(status('real 240 x 120, rotation 3')['ready'])
            self.assertIsNone(phone.observation)
            self.assertFalse(status('real 242 x 120, rotation 1')['ready'])
            self.assertFalse(status('real 120 x 240')['ready'])

    def test_windows_keep_displays_separate(self):
        dump = ('Display: mDisplayId=0\n  mCurrentFocus=Window{aaa u0 moon/Game}\n'
                '  Display: mDisplayId=8\n  mFocusedApp=ActivityRecord{bbb u0 com.qq/.Main t22}\n')
        self.assertEqual(device._windows(dump), {0: ('moon/Game',''), 8: ('','com.qq/com.qq.Main')})

    def test_status_compares_full_android_components_and_pins_the_primary(self):
        package = 'org.example.player'
        primary = package + '/.ui.Player$Activity'
        expanded = package + '/' + package + '.ui.Player$Activity'
        other = package + '/.Other'
        def dump(focused, app):
            return (f'Display: mDisplayId=0\n  mCurrentFocus={focused}\n  mFocusedApp={app}\n'
                    'Display: mDisplayId=8\n  mCurrentFocus=Window{abc u0 org.calculator/.Main}\n')

        with tempfile.TemporaryDirectory() as root, patch.object(Path, 'is_socket', return_value=True), \
                patch.object(device, '_android') as android:
            phone = device.PhoneDevice()
            phone.runtime = Path(root)
            (phone.runtime / 'display.json').write_text('{"id":8,"width":160,"height":120}')
            (phone.runtime / 'screen.sock').touch()
            def status(focus, app):
                android.side_effect = ['Display id 8: scrcpy, real 160 x 120, rotation 0', dump(focus, app)]
                return phone.status()

            first = status(f'Window{{a u0 {expanded}}}', f'ActivityRecord{{b u0 {primary} t23}}')
            self.assertTrue(first['ready'], first)
            self.assertEqual(first['activity'], 'org.calculator/org.calculator.Main')
            self.assertEqual(phone.primary, expanded)
            self.assertTrue(status(f'Window{{a u0 {primary}}}', f'ActivityRecord{{b u0 {expanded} t23}}')['ready'])
            for win, app in [(primary, other), (primary, 'some.other/'+package+'.ui.Player$Activity'),
                             ('NotificationShade', primary), (primary, 'broken/name/impossible')]:
                with self.subTest(win=win,app=app):
                    self.assertFalse(status(f'Window{{a u0 {win}}}', f'ActivityRecord{{b u0 {app} t23}}')['ready'])
            self.assertFalse(status('null', 'null')['ready'])
            self.assertFalse(status(f'Window{{a u0 {other}}}', f'ActivityRecord{{b u0 {other} t23}}')['ready'])

    def test_ocr_grouped_by_line(self):
        table = ('level\tblock_num\tpar_num\tline_num\tleft\ttop\twidth\theight\ttext\n'
                 '5\t1\t1\t1\t3\t4\t5\t6\tHello\n'
                 '5\t1\t1\t1\t10\t4\t5\t6\tworld\n')
        with patch.object(device.subprocess, 'run', return_value=MagicMock(stdout=table.encode())):
            elems, warning = device._ocr(b'bytes', 160,120)
        self.assertIsNone(warning)
        self.assertEqual(elems, [{'id':'e1','text':'Hello world','bounds':[3,4,15,10]}])


if __name__ == '__main__':
    unittest.main()
