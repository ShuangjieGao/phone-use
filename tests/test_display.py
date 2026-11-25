import argparse
import contextlib
import io
import socket
import struct
import threading
import unittest
import zlib
from unittest.mock import patch

from xfy_phone_use import display


def png_chunk(tag, data):
    return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))


def tiny_png():
    return (display.PNG + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
            + png_chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00')) + png_chunk(b'IEND', b''))


class RuntimeTests(unittest.TestCase):
    def test_run_requires_an_explicit_app(self):
        errors = io.StringIO()
        args = ['phone-display', 'run', '--server-jar', '/example/server.jar']
        with patch('sys.argv', args), contextlib.redirect_stderr(errors):
            with self.assertRaises(SystemExit) as raised:
                display.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('run requires --package', errors.getvalue())

    def test_fragmented_request_and_exact_existing_frame_header(self):
        frames = display.Frames()
        frame = tiny_png()
        frames.decode(io.BytesIO(frame * 2))
        left, right = socket.socketpair()
        with left, right, left.makefile('rb') as reader:
            server = threading.Thread(target=frames.reply, args=(right,))
            server.start()
            left.sendall(b'FR')
            left.sendall(b'AME')
            n, seq, stamp = struct.unpack('>IQQ', reader.read(20))
            self.assertEqual(n, len(frame))
            self.assertEqual(seq, 2)
            self.assertGreater(stamp, 0)
            self.assertEqual(reader.read(n), frame)
            server.join(2)
            self.assertFalse(server.is_alive())

    def test_bad_png_frame_rejected(self):
        for frame in (b'bad data', tiny_png()[:-8], display.PNG + b'\xff\xff\xff\xffIDAT'):
            with self.subTest(frame=frame[:8]), self.assertRaises(ValueError):
                display.read_png(io.BytesIO(frame))

    def test_display_must_be_new_unique_secondary_with_exact_geometry(self):
        desc = 'DisplayInfo{"scrcpy", real 1080 x 1920, density 420}'
        before = {0: 'primary', 2: desc}
        self.assertIsNone(display.new_display(before, before, 1080, 1920))
        self.assertIsNone(display.new_display({}, {0: desc}, 1080, 1920))
        self.assertEqual(display.new_display(before, {**before, 3: desc}, 1080, 1920), 3)
        with self.assertRaises(RuntimeError):
            display.new_display(before, {**before, 3: desc, 4: desc}, 1080, 1920)
        with self.assertRaises(RuntimeError):
            display.new_display(before, {**before, 3: desc}, 1920, 1080)

    def test_portrait_options_do_not_set_primary_display(self):
        args = display.server_options('2a000012', argparse.Namespace(width=1080, height=1920, density=420))
        self.assertIn('new_display=1080x1920/420', args)
        self.assertIn('vd_destroy_content=true', args)
        self.assertIn('power_on=false', args)
        self.assertFalse(any(arg.startswith('display_id=') for arg in args))


if __name__ == '__main__':
    unittest.main()
