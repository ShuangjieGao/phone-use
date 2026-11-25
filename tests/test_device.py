import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from xfy_phone_use import device


class PhoneDeviceTests(unittest.TestCase):
    def setUp(self):
        self.phone = device.PhoneDevice()
        self.phone.runtime = Path('runtime')
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

    def test_frame_only_observation_skips_ocr(self):
        with patch.object(device, '_ocr') as ocr:
            view = self.phone.observe(elements=False, screenshot=True)
        ocr.assert_not_called()
        self.assertEqual(view['source'], 'frame')
        self.assertEqual(view['elements'], [])
        self.assertEqual(view['image_png'], self.png)

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
        self.assertTrue(device._same_tap_target(before,encode(image),(24,24,120,120)))
        painter.rectangle((70,60,79,66),fill='black')
        self.assertFalse(device._same_tap_target(before,encode(image),(24,24,120,120)))
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

    def test_windows_keep_displays_separate(self):
        dump = ('Display: mDisplayId=0\n  mCurrentFocus=Window{aaa u0 moon/Game}\n'
                '  Display: mDisplayId=8\n  mFocusedApp=ActivityRecord{bbb u0 com.qq/.Main t22}\n')
        self.assertEqual(device._windows(dump), {0: ('moon/Game',''), 8: ('','com.qq/.Main')})

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
