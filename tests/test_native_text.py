import subprocess
import os
import tempfile
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from xfy_phone_use import native_text


def result(stdout=b'', code=0):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=b'')


class NativeTextTransportTests(unittest.TestCase):
    def setUp(self):
        flag = patch.object(native_text, '_service_enabled', return_value=False)
        self.service = flag.start()
        self.addCleanup(flag.stop)

    @staticmethod
    @contextmanager
    def mailbox(request):
        yield 42, 'a' * 32

    def test_request_body_only_uses_the_private_mailbox(self):
        reply = b'INSTRUMENTATION_RESULT: phone_use={"status":"verified"}\nINSTRUMENTATION_CODE: -1\n'
        with patch.object(native_text, '_mailbox', wraps=self.mailbox) as mailbox, \
                patch.object(native_text, '_android_environment', return_value={}), \
                patch.object(subprocess, 'run', return_value=result(reply)) as invoke:
            request = {'op': 'insert', 'display': 10, 'package': 'org.example.app',
                       'token': 'private-token', 'text': '中文 🌱'}
            self.assertEqual(native_text._exchange(request)['status'], 'verified')
        mailbox.assert_called_once_with(request)
        self.assertEqual(invoke.call_count, 1)
        command = invoke.call_args.args[0]
        self.assertIn('a' * 32, command)
        self.assertNotIn(request['text'], ' '.join(command))
        self.assertNotIn(request['token'], ' '.join(command))
        self.assertEqual(invoke.call_args.kwargs['stdin'], subprocess.DEVNULL)

    def test_text_is_not_applied_when_request_cannot_be_staged(self):
        req = {'op': 'insert', 'text': '中文'}
        with patch.object(native_text, '_mailbox', side_effect=PermissionError), \
                patch.object(native_text, '_android_environment', return_value={}), \
                patch.object(native_text, '_invoke') as invoke:
            self.assertEqual(native_text._exchange(req)['status'], 'not_applied')
            invoke.assert_not_called()

    def test_lost_or_malformed_reply_during_insert_is_unconfirmed(self):
        req = {'op': 'insert', 'text': '中文'}
        for interrupted in [result(b'instrumentation failed'), result(code=1),
                            subprocess.TimeoutExpired(['instrument'], timeout=12)]:
            with self.subTest(interrupted=interrupted), patch.object(native_text, '_mailbox', self.mailbox), \
                    patch.object(native_text, '_android_environment', return_value={}), \
                    patch.object(native_text, '_invoke', side_effect=[interrupted]) as invoke:
                response = native_text._exchange(req)
                self.assertEqual(response['status'], 'unconfirmed')
                self.assertEqual(invoke.call_count, 1)

    def test_enabled_service_never_launches_instrumentation(self):
        self.service.return_value = True
        req = {'op': 'insert', 'display': 10, 'package': 'org.example.app'}
        with patch.object(native_text, '_android_environment', return_value={}), \
                patch.object(native_text, '_service_exchange', return_value={'status':'unconfirmed'}) as resident, \
                patch.object(native_text, '_invoke') as standalone:
            self.assertEqual(native_text._exchange(req, 'input_connection')['status'], 'unconfirmed')
            self.assertEqual(native_text._exchange(req, 'instrumentation')['reason'], 'transport_changed')
            resident.assert_called_once_with(req, {})
            standalone.assert_not_called()

    def test_ime_must_match_current_client_focused_client_and_editor(self):
        record = 'abc uid=10233 pid=444 displayId=10'
        text = (f'  mCurClient=ClientState{{{record}}} mCurSeq=99\n'
                f'  mCurFocusedWindow=android.os.BinderProxy@abc softInputMode=STATE_HIDDEN '
                f'client=ClientState{{{record}}}\n  mCurrentTextBoxAttribute:\n'
                '    inputType=10\n    packageName=org.example.app fieldId=4\n'
                '  History:\n    packageName=old.editor\n')
        self.assertTrue(native_text._client_target(text, 10, 10233, 'org.example.app'))
        for changed in (text.replace('displayId=10', 'displayId=0', 1),
                        text.replace('abc uid=10233', 'xyz uid=10233', 1),
                        text.replace('pid=444', 'pid=555', 1),
                        text.replace('uid=10233', 'uid=56789'),
                        text.replace('packageName=org.example.app', 'privateOption=x'),
                        text.replace('mCurrentTextBoxAttribute:', 'OldAttribute:')):
            with self.subTest(changed=changed):
                self.assertFalse(native_text._client_target(changed, 10, 10233, 'org.example.app'))
        self.assertFalse(native_text._client_target(text, 10, 10233, 'old.editor'))

    def test_service_rechecks_target_after_receiving_verified_receipt(self):
        req = {'op': 'insert', 'display': 10, 'package': 'org.example.app', 'text': '中文'}
        for after, expected in ((True, 'verified'), (False, 'unconfirmed')):
            with self.subTest(after=after), tempfile.TemporaryFile() as out:
                out.write(b'{"status":"verified"}'); out.seek(0)
                @contextmanager
                def box(item, *, service):
                    self.assertTrue(service)
                    self.assertIn('expires_ms', item)
                    yield out.fileno(), 'b'*32
                with patch.object(native_text, '_mailbox', side_effect=box), \
                        patch.object(native_text, '_system_output', return_value='package:org.example.app uid:10233'), \
                        patch.object(native_text, '_ime_matches', side_effect=[True, after]), \
                        patch.object(os, 'rename') as dispatch, patch.object(os, 'O_NOFOLLOW', 0, create=True), \
                        patch.object(os, 'open', return_value=os.dup(out.fileno())):
                    self.assertEqual(native_text._service_exchange(req, {})['status'], expected)
                    dispatch.assert_called_once()


if __name__ == '__main__':
    unittest.main()
