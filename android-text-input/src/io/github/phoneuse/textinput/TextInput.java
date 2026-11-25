package io.github.phoneuse.textinput;

import android.accessibilityservice.AccessibilityServiceInfo;
import android.app.Activity;
import android.app.Instrumentation;
import android.app.UiAutomation;
import android.graphics.Rect;
import android.os.Bundle;
import android.os.SystemClock;
import android.text.InputType;
import android.view.accessibility.AccessibilityNodeInfo;
import android.view.accessibility.AccessibilityWindowInfo;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.ArrayDeque;
import java.util.List;

/** One local request, one focused editor on an explicitly selected secondary display. */
public final class TextInput extends Instrumentation {
    private String requestId;
    private boolean attempted;

    @Override public void onCreate(Bundle arguments) {
        super.onCreate(arguments);
        requestId = arguments == null ? null : arguments.getString("request");
        start();
    }

    private static final class Refusal extends Exception {
        Refusal(String reason) { super(reason); }
    }

    private JSONObject request() throws Exception {
        if (requestId == null || !requestId.matches("[a-f0-9]{32}")) throw new Refusal("bad_request");
        return readOnce(new File(getTargetContext().getFilesDir(), requestId + ".json"));
    }

    static JSONObject readOnce(File file) throws Exception {
        if (file.length() == 0 || file.length() > 65536) throw new Refusal("bad_request");
        try (FileInputStream in = new FileInputStream(file)) {
            // Unlink before processing; only the app and its adb run-as identity can access this path.
            if (!file.delete()) throw new Refusal("bad_request");
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            int count;
            while ((count = in.read(chunk)) != -1) {
                if (bytes.size() + count > 65536) throw new Refusal("bad_request");
                bytes.write(chunk, 0, count);
            }
            return new JSONObject(new String(bytes.toByteArray(), StandardCharsets.UTF_8));
        }
    }

    static JSONObject result(String status, String reason) {
        JSONObject out = new JSONObject();
        try {
            out.put("status", status);
            if (reason != null) out.put("reason", reason);
        } catch (org.json.JSONException impossible) { throw new AssertionError(impossible); }
        return out;
    }

    @Override public void onStart() {
        JSONObject answer;
        try {
            answer = handle(request());
        } catch (Refusal e) {
            answer = result(attempted ? "unconfirmed" : "not_applied", e.getMessage());
        } catch (Exception e) {
            // Exceptions and system messages may contain the edit contents. Return only category.
            answer = result(attempted ? "unconfirmed" : "not_applied",
                            attempted ? "dispatch_uncertain" : "automation_unavailable");
        }
        Bundle bundle = new Bundle();
        bundle.putString("phone_use", answer.toString());
        finish(Activity.RESULT_OK, bundle);
    }

    private static boolean password(AccessibilityNodeInfo node) {
        return node.isPassword() || passwordType(node.getInputType());
    }

    static boolean passwordType(int input) {
        int kind = input & InputType.TYPE_MASK_CLASS;
        int variation = input & InputType.TYPE_MASK_VARIATION;
        return (kind == InputType.TYPE_CLASS_NUMBER && variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD)
            || (kind == InputType.TYPE_CLASS_TEXT && (variation == InputType.TYPE_TEXT_VARIATION_PASSWORD
                || variation == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD || variation == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD));
    }

    private static final class Editor {
        final AccessibilityNodeInfo node;
        final Rect bounds = new Rect();
        final String identity, text;
        final int start, end;

        Editor(AccessibilityNodeInfo node, int display, String pkg) throws Exception {
            this.node = node;
            node.getBoundsInScreen(bounds);
            if (bounds.isEmpty()) throw new Refusal("editor_unavailable");
            // AOSP node hash includes the view, virtual descendant and accessibility window IDs.
            identity = new JSONArray().put(display).put(pkg).put(node.getWindowId()).put(node.hashCode())
                .put(node.getViewIdResourceName()).put(String.valueOf(node.getClassName())).put(node.getInputType())
                .put(bounds.flattenToString()).toString();
            CharSequence value = node.isShowingHintText() ? null : node.getText();
            text = value == null ? "" : value.toString();
            if (text.length() > 65536) throw new Refusal("editor_unavailable");
            start = text.isEmpty() ? 0 : node.getTextSelectionStart();
            end = text.isEmpty() ? 0 : node.getTextSelectionEnd();
        }

        String token() throws Exception {
            return digest(new JSONArray().put(identity).put(text).put(start).put(end).toString());
        }
    }

    static String digest(String snapshot) throws Exception {
        byte[] hash = MessageDigest.getInstance("SHA-256").digest(snapshot.getBytes(StandardCharsets.UTF_8));
        char[] hex = "0123456789abcdef".toCharArray();
        StringBuilder out = new StringBuilder(64);
        for (byte b : hash) out.append(hex[(b & 255) >>> 4]).append(hex[b & 15]);
        return out.toString();
    }

    private static Editor editor(UiAutomation automation, int display, String pkg) throws Exception {
        List<AccessibilityWindowInfo> windows = automation.getWindowsOnAllDisplays().get(display);
        if (windows == null || windows.isEmpty()) throw new Refusal("display_missing");
        AccessibilityWindowInfo top = null;
        for (AccessibilityWindowInfo window : windows) {
            if (window.getDisplayId() != display) throw new Refusal("display_missing");
            if (window.getType() == AccessibilityWindowInfo.TYPE_INPUT_METHOD) continue;
            if (window.getType() != AccessibilityWindowInfo.TYPE_APPLICATION) {
                if (window.isFocused()) throw new Refusal("editor_unavailable");
                continue;
            }
            if (top == null || window.getLayer() > top.getLayer()) top = window;
            else if (window.getLayer() == top.getLayer()) throw new Refusal("ambiguous_editor");
        }
        if (top == null) throw new Refusal("editor_unavailable");
        AccessibilityNodeInfo root = top.getRoot();
        if (root == null || !pkg.contentEquals(String.valueOf(root.getPackageName()))) throw new Refusal("editor_unavailable");
        AccessibilityNodeInfo n = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (n == null || !n.refresh() || n.getWindowId() != top.getId() || !pkg.contentEquals(String.valueOf(n.getPackageName()))
                || !n.isFocused() || !n.isVisibleToUser() || !n.isEnabled() || !n.isEditable()) throw new Refusal("editor_unavailable");
        if (password(n)) throw new Refusal("password_field");
        if (!n.getActionList().contains(AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_TEXT)) throw new Refusal("editor_unavailable");
        return new Editor(n, display, pkg);
    }

    // Local troubleshooting only: never ask nodes for text, descriptions, IDs or selection.
    private static JSONObject diagnose(UiAutomation auto, int display, String pkg) throws Exception {
        JSONArray records = new JSONArray();
        List<AccessibilityWindowInfo> windows = auto.getWindowsOnAllDisplays().get(display);
        if (windows != null) for (AccessibilityWindowInfo w : windows) {
            if (w.getDisplayId() != display) throw new Refusal("display_missing");
            JSONObject row = new JSONObject().put("type", w.getType()).put("layer", w.getLayer())
                .put("focused", w.isFocused()).put("active", w.isActive());
            records.put(row);
            if (w.getType() != AccessibilityWindowInfo.TYPE_APPLICATION) continue;
            AccessibilityNodeInfo root = w.getRoot();
            row.put("has_root", root != null);
            if (root == null) continue;
            boolean right = pkg.contentEquals(String.valueOf(root.getPackageName()));
            row.put("package_matches", right);
            if (!right) continue;
            boolean refreshed = root.refresh();
            row.put("root_refreshed", refreshed).put("root_children", root.getChildCount())
                    .put("root_visible", root.isVisibleToUser()).put("root_important", root.isImportantForAccessibility());
            AccessibilityNodeInfo f = root.findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
            row.put("find_focus", f != null);
            if (f != null) row.put("find_focus_is_root", f.equals(root)).put("find_focus_editable", f.isEditable()).put("find_focus_is_focused", f.isFocused())
                    .put("find_focus_visible", f.isVisibleToUser()).put("find_focus_password", password(f))
                    .put("find_focus_set_text", f.getActionList().contains(AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_TEXT));
            int visited = 0, edit = 0, focusedEdit = 0, visibleSetText = 0, focusedSetText = 0;
            ArrayDeque<AccessibilityNodeInfo> queue = new ArrayDeque<>();
            queue.add(root);
            while (!queue.isEmpty() && visited++ < 400) {
                AccessibilityNodeInfo n = queue.remove();
                if (n.getWindowId() != w.getId() || !pkg.contentEquals(String.valueOf(n.getPackageName()))) continue;
                if (n.isEditable()) {
                    edit++;
                    if (n.isFocused()) focusedEdit++;
                    if (n.isVisibleToUser() && n.isEnabled() && !password(n)
                        && n.getActionList().contains(AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_TEXT)) {
                        visibleSetText++;
                        if (n.isFocused()) focusedSetText++;
                    }
                }
                for (int i = 0; i < n.getChildCount() && queue.size() < 400; i++) {
                    AccessibilityNodeInfo child = n.getChild(i);
                    if (child != null) queue.add(child);
                }
            }
            row.put("visited", visited).put("editables", edit).put("focused_editables", focusedEdit)
                .put("visible_set_text_editables", visibleSetText).put("focused_set_text_editables", focusedSetText);
        }
        return result("diagnostic", null).put("windows", records);
    }

    private JSONObject handle(JSONObject req) throws Exception {
        int display = req.optInt("display", -1);
        String pkg = req.optString("package", "");
        String op = req.optString("op", "");
        if (display <= 0 || !pkg.matches("[A-Za-z]\\w*(?:\\.[A-Za-z]\\w*)+")
                || !(op.equals("inspect") || op.equals("insert") || op.equals("diagnose"))) throw new Refusal("bad_request");
        UiAutomation auto = getUiAutomation(UiAutomation.FLAG_DONT_SUPPRESS_ACCESSIBILITY_SERVICES);
        if (auto == null) throw new Refusal("automation_unavailable");
        AccessibilityServiceInfo cfg = auto.getServiceInfo();
        cfg.flags |= AccessibilityServiceInfo.FLAG_RETRIEVE_INTERACTIVE_WINDOWS | AccessibilityServiceInfo.FLAG_REPORT_VIEW_IDS
                | AccessibilityServiceInfo.FLAG_INCLUDE_NOT_IMPORTANT_VIEWS;
        auto.setServiceInfo(cfg);
        if (op.equals("diagnose")) {
            JSONArray samples = new JSONArray();
            for (int i = 0; i < 3; i++) {
                SystemClock.sleep(450);
                samples.put(diagnose(auto, display, pkg).getJSONArray("windows"));
            }
            return result("diagnostic", null).put("samples", samples);
        }
        try { auto.waitForIdle(100, 750); } catch (java.util.concurrent.TimeoutException ignored) { }
        // The window list arrives asynchronously after enabling interactive-window retrieval.
        long availableBy = SystemClock.uptimeMillis() + 750;
        Editor e;
        while (true) {
            try {
                e = editor(auto, display, pkg);
                break;
            } catch (Refusal unavailable) {
                if (!"display_missing".equals(unavailable.getMessage()) || SystemClock.uptimeMillis() >= availableBy) throw unavailable;
                SystemClock.sleep(50);
            }
        }
        if (op.equals("inspect")) {
            return result("ready", null).put("token", e.token()).put("bounds", new JSONArray()
                .put(e.bounds.left).put(e.bounds.top).put(e.bounds.right).put(e.bounds.bottom));
        }
        String addition = req.optString("text", "");
        if (addition.isEmpty() || addition.length() > 4096) throw new Refusal("bad_request");
        for (int i = 0; i < addition.length(); i++) if (Character.isISOControl(addition.charAt(i))) throw new Refusal("bad_request");
        if (!e.token().equals(req.optString("token", ""))) throw new Refusal("target_changed");
        int first = Math.min(e.start, e.end), last = Math.max(e.start, e.end);
        if (first < 0 || last > e.text.length()) throw new Refusal("unknown_selection");
        String expected = e.text.substring(0, first) + addition + e.text.substring(last);
        int caret = first + addition.length();
        if (caret < expected.length() && !e.node.getActionList().contains(AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_SELECTION))
            throw new Refusal("unknown_selection");
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, expected);
        attempted = true;
        e.node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args);
        boolean positioned = false;
        long until = SystemClock.uptimeMillis() + 1700;
        do {
            Editor current = editor(auto, display, pkg);
            if (!current.identity.equals(e.identity)) throw new Refusal("target_changed");
            if (expected.equals(current.text)) {
                if (current.start == caret && current.end == caret) return result("verified", null).put("selection_verified", true);
                if (caret == expected.length() && current.start < 0 && current.end < 0)
                    return result("verified", null).put("selection_verified", false);
                if (!positioned && current.node.getActionList().contains(AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_SELECTION)) {
                    Bundle selection = new Bundle();
                    selection.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, caret);
                    selection.putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, caret);
                    current.node.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, selection);
                    positioned = true;
                }
            }
            SystemClock.sleep(55);
        } while (SystemClock.uptimeMillis() < until);
        return result("unconfirmed", "readback_failed");
    }
}
