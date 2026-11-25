package io.github.phoneuse.textinput;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.InputMethod;
import android.os.FileObserver;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.text.InputType;
import android.text.Spanned;
import android.view.accessibility.AccessibilityEvent;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;
import android.view.inputmethod.SurroundingText;

import org.json.JSONArray;
import org.json.JSONObject;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.util.UUID;

/** Android 13 accessibility input connection. No view-tree, clipboard, network or screenshots. */
public final class EditorService extends AccessibilityService {
    private final Handler main = new Handler(Looper.getMainLooper());
    private final String instance = UUID.randomUUID().toString();
    private long generation;
    private FileObserver inbox;
    private String inFlight;

    @Override public InputMethod onCreateInputMethod() {
        return new InputMethod(this) {
            @Override public void onStartInput(EditorInfo attribute, boolean restarting) {
                super.onStartInput(attribute, restarting);
                generation++;
            }
            @Override public void onFinishInput() {
                generation++;
                super.onFinishInput();
            }
        };
    }

    @Override protected void onServiceConnected() {
        if (inbox != null) inbox.stopWatching();
        inbox = new FileObserver(getFilesDir(), FileObserver.MOVED_TO) {
            @Override public void onEvent(int event, final String name) {
                if ((event & FileObserver.MOVED_TO) != 0 && name != null && name.matches("[a-f0-9]{32}\\.ime"))
                    main.post(new Runnable() { public void run() { process(name.substring(0, 32)); } });
            }
        };
        inbox.startWatching();
    }

    @Override public void onDestroy() {
        if (inbox != null) inbox.stopWatching();
        main.removeCallbacksAndMessages(null);
        super.onDestroy();
    }

    @Override public void onAccessibilityEvent(AccessibilityEvent unused) { }
    @Override public void onInterrupt() { }

    private static final class Denied extends Exception {
        Denied(String reason) { super(reason); }
    }

    private static final class Snapshot {
        final InputMethod.AccessibilityInputConnection connection;
        final String identity, text;
        final int start, end, offset;

        Snapshot(InputMethod.AccessibilityInputConnection connection, String identity, SurroundingText s) throws Exception {
            this.connection = connection;
            this.identity = identity;
            if (s == null || s.getText() == null) throw new Denied("surrounding_text_unavailable");
            CharSequence contents = s.getText();
            // IME commitText replaces a composing span, even when the caret is elsewhere.
            if (contents instanceof Spanned) {
                Spanned spans = (Spanned) contents;
                for (Object part : spans.getSpans(0, spans.length(), Object.class))
                    if ((spans.getSpanFlags(part) & Spanned.SPAN_COMPOSING) != 0) throw new Denied("unknown_selection");
            }
            text = contents.toString();
            start = s.getSelectionStart();
            end = s.getSelectionEnd();
            offset = s.getOffset();
            if (start < 0 || end < 0 || Math.max(start, end) > text.length()) throw new Denied("unknown_selection");
            // Avoid ambiguous comparisons at a truncated surrounding-text boundary.
            if (text.length() >= 4096 || offset > 0) throw new Denied("surrounding_text_unavailable");
        }

        String token() throws Exception {
            return TextInput.digest(new JSONArray().put(identity).put(text).put(start).put(end).put(offset).toString());
        }
    }

    private Snapshot snapshot(int display, String pkg) throws Exception {
        InputMethod im = getInputMethod();
        if (im == null || !im.getCurrentInputStarted()) throw new Denied("input_session_missing");
        EditorInfo info = im.getCurrentInputEditorInfo();
        if (info == null || !pkg.equals(info.packageName)) throw new Denied("input_package_mismatch");
        if (TextInput.passwordType(info.inputType)) throw new Denied("password_field");
        if ((info.inputType & InputType.TYPE_MASK_CLASS) == InputType.TYPE_NULL) throw new Denied("unsupported_editor");
        InputMethod.AccessibilityInputConnection ic = im.getCurrentInputConnection();
        if (ic == null) throw new Denied("input_session_missing");
        String id = new JSONArray().put(instance).put(generation).put(display).put(pkg)
                .put(info.fieldId).put(info.inputType).toString();
        return new Snapshot(ic, id, ic.getSurroundingText(4096, 4096, InputConnection.GET_TEXT_WITH_STYLES));
    }

    private void reply(String nonce, JSONObject result) {
        if (nonce.equals(inFlight)) inFlight = null;
        File tmp = new File(getFilesDir(), nonce + ".reply");
        File out = new File(getFilesDir(), nonce + ".out");
        try {
            result.put("transport", "input_connection");
            try (FileOutputStream stream = new FileOutputStream(tmp)) {
                stream.write(result.toString().getBytes(StandardCharsets.UTF_8));
            }
            if (!tmp.renameTo(out)) tmp.delete();
        } catch (Exception failed) { tmp.delete(); }
    }

    private void process(final String nonce) {
        if (nonce.equals(inFlight)) return;
        boolean dispatched = false;
        try {
            JSONObject req = TextInput.readOnce(new File(getFilesDir(), nonce + ".ime"));
            if (inFlight != null) throw new Denied("target_changed");
            int display = req.optInt("display", -1);
            String pkg = req.optString("package", "");
            String op = req.optString("op", "");
            if (display <= 0 || !pkg.matches("[A-Za-z]\\w*(?:\\.[A-Za-z]\\w*)+")) throw new Denied("bad_request");
            if (!op.equals("inspect") && !op.equals("insert")) throw new Denied("bad_request");
            Snapshot before = snapshot(display, pkg);
            if (op.equals("inspect")) {
                reply(nonce, TextInput.result("ready", null).put("token", before.token()));
                return;
            }
            String addition = req.optString("text", "");
            if (addition.isEmpty() || before.text.length() + addition.length() >= 4096) throw new Denied("bad_request");
            for (int i = 0; i < addition.length(); i++) if (Character.isISOControl(addition.charAt(i))) throw new Denied("bad_request");
            if (!before.token().equals(req.optString("token", ""))) throw new Denied("target_changed");
            long left = req.optLong("expires_ms", 0) - System.currentTimeMillis();
            if (left <= 0 || left > 10000) throw new Denied("target_changed");
            int first = Math.min(before.start, before.end), last = Math.max(before.start, before.end);
            String expected = before.text.substring(0, first) + addition + before.text.substring(last);
            inFlight = nonce;
            dispatched = true;
            before.connection.commitText(addition, 1, null);
            verify(nonce, display, pkg, before, expected, first + addition.length(), SystemClock.uptimeMillis() + 1700);
        } catch (Exception error) {
            String reason = error instanceof Denied ? error.getMessage() : (dispatched ? "dispatch_uncertain" : "automation_unavailable");
            reply(nonce, TextInput.result(dispatched ? "unconfirmed" : "not_applied", reason));
        }
    }

    private void verify(final String nonce, final int display, final String pkg, final Snapshot before,
                        final String expected, final int caret, final long until) {
        // Queue the read so incoming system editor-session callbacks can run between checks.
        main.postDelayed(new Runnable() { public void run() {
            try {
                Snapshot now = snapshot(display, pkg);
                if (!before.identity.equals(now.identity)) throw new Denied("target_changed");
                if (now.offset == before.offset && expected.equals(now.text)) {
                    reply(nonce, TextInput.result("verified", null).put("selection_verified", now.start == caret && now.end == caret));
                } else if (SystemClock.uptimeMillis() < until) {
                    verify(nonce, display, pkg, before, expected, caret, until);
                } else reply(nonce, TextInput.result("unconfirmed", "readback_failed"));
            } catch (Exception error) {
                reply(nonce, TextInput.result("unconfirmed", "readback_failed"));
            }
        }}, 55);
    }
}
