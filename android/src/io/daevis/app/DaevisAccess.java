package io.daevis.app;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Path;
import android.graphics.Rect;
import android.os.Build;
import android.os.Bundle;
import android.view.accessibility.AccessibilityEvent;
import android.view.accessibility.AccessibilityNodeInfo;

import java.util.ArrayList;
import java.util.List;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Lets Daevis read and operate this phone's screen, one step per phone_task job.
 * It reacts to no events on its own (nothing runs between jobs, so no battery cost).
 */
public final class DaevisAccess extends AccessibilityService {
    static volatile DaevisAccess instance;
    private final List<AccessibilityNodeInfo> nodes = new ArrayList<>();
    private static final int MAX_NODES = 250;

    private final android.os.Handler main = new android.os.Handler(android.os.Looper.getMainLooper());
    private volatile boolean wokeUs;
    private volatile String unlockStage = "not_attempted";
    private final Runnable relock = () -> {
        if (wokeUs) { wokeUs = false; performGlobalAction(GLOBAL_ACTION_LOCK_SCREEN); }
    };

    @Override protected void onServiceConnected() { instance = this; }

    /**
     * Before a step: if the screen is off or a lock screen without PIN is up, turn it on and
     * dismiss it. Returns false when a password is needed; unlock() handles that separately.
     */
    boolean ensureAwake() throws InterruptedException {
        main.removeCallbacks(relock);
        android.app.KeyguardManager km = getSystemService(android.app.KeyguardManager.class);
        android.os.PowerManager pm = getSystemService(android.os.PowerManager.class);
        if (km.isDeviceLocked()) return false;
        if (!pm.isInteractive() || km.isKeyguardLocked()) {
            wokeUs = true;
            startActivity(new Intent(this, WakeActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_NO_ANIMATION));
            for (int i = 0; i < 20 && (km.isKeyguardLocked() || !pm.isInteractive()); i++) Thread.sleep(150);
        }
        return !km.isKeyguardLocked();
    }

    /** After a step: lock again 20 s later if Daevis was the one that unlocked. */
    void scheduleRelock() {
        main.removeCallbacks(relock);
        if (wokeUs) main.postDelayed(relock, 20000);
    }

    /** Fetch the credential only after finding the system password field, then try exactly once. */
    boolean unlock(String node, String token, String job) {
        android.app.KeyguardManager km = getSystemService(android.app.KeyguardManager.class);
        unlockStage = "starting";
        boolean success = UnlockFlow.run(new UnlockFlow.Device() {
            AccessibilityNodeInfo field;
            public boolean locked() { return km.isKeyguardLocked(); }
            public boolean prepare() throws Exception {
                wokeUs = true;
                startActivity(new Intent(DaevisAccess.this, WakeActivity.class).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_NO_ANIMATION));
                unlockStage = "finding_input";
                for (int i=0; i<35; i++) {
                    field = findPasswordField();
                    if (field != null) return true;
                    if (i == 15) {
                        android.util.DisplayMetrics m=getResources().getDisplayMetrics();
                        swipe(new JSONObject().put("x1",m.widthPixels/2).put("y1",m.heightPixels*4/5)
                            .put("x2",m.widthPixels/2).put("y2",m.heightPixels/4).put("duration",350));
                    }
                    Thread.sleep(160);
                }
                unlockStage = "input_not_found";
                return false;
            }
            public String credential() throws Exception {
                unlockStage = "fetching_credential";
                return Relay.send("/phone/unlock-secret", new JSONObject().put("node",node).put("id",job), token).getString("password");
            }
            public boolean enter(String password) throws Exception {
                unlockStage = "entering";
                if (field == null || !field.refresh() || !isPasswordField(field)) return false;
                Bundle args = new Bundle();
                args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, password);
                field.performAction(AccessibilityNodeInfo.ACTION_FOCUS);
                if (!field.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT,args)) return false;
                if (Build.VERSION.SDK_INT >= 30 && field.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.getId())) return true;
                for (AccessibilityNodeInfo root : roots()) {
                    for (String id : new String[]{"com.android.systemui:id/key_enter", "com.android.systemui:id/enter_key", "com.android.systemui:id/keyguard_enter"}) {
                        for (AccessibilityNodeInfo n : root.findAccessibilityNodeInfosByViewId(id))
                            if (n.isVisibleToUser() && n.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return true;
                    }
                }
                return false;
            }
            public void waitForUnlock() throws Exception {
                unlockStage = "verifying";
                for(int i=0; i<25 && km.isKeyguardLocked(); i++) Thread.sleep(160);
            }
        });
        if (success) unlockStage = "unlocked";
        return success;
    }

    private List<AccessibilityNodeInfo> roots() {
        List<AccessibilityNodeInfo> all = new ArrayList<>();
        AccessibilityNodeInfo active=getRootInActiveWindow();
        if (active != null) all.add(active);
        for (android.view.accessibility.AccessibilityWindowInfo w : getWindows()) {
            AccessibilityNodeInfo root=w.getRoot();
            if (root != null) all.add(root);
        }
        return all;
    }

    private boolean isPasswordField(AccessibilityNodeInfo n) {
        boolean setText=n.getActionList().contains(AccessibilityNodeInfo.AccessibilityAction.ACTION_SET_TEXT);
        return UnlockPolicy.field(n.getPackageName()==null ? null : n.getPackageName().toString(),n.getViewIdResourceName(),
            n.isVisibleToUser(),n.isPassword(),n.isEditable(),setText);
    }

    private AccessibilityNodeInfo findPasswordField() {
        for (AccessibilityNodeInfo root : roots()) {
            AccessibilityNodeInfo field=passwordField(root,0,new int[]{0});
            if (field != null) return field;
        }
        return null;
    }

    private AccessibilityNodeInfo passwordField(AccessibilityNodeInfo n,int depth,int[] count) {
        if (n == null || depth>40 || ++count[0]>1500) return null;
        if (isPasswordField(n)) return n;
        for (int i=0; i<n.getChildCount(); i++) {
            AccessibilityNodeInfo found=passwordField(n.getChild(i),depth+1,count);
            if (found != null) return found;
        }
        return null;
    }

    JSONObject lockState() throws JSONException {
        JSONArray windows=new JSONArray();
        for(AccessibilityNodeInfo root:roots()) windows.put(cut(root.getPackageName(),80));
        return new JSONObject().put("locked",true).put("nodes",new JSONArray()).put("unlock_stage",unlockStage).put("window_packages",windows);
    }

    /** Captures actual pixels for the agent; secure windows return a specific error, never a fake picture. */
    byte[] screenshot() throws Exception {
        if (Build.VERSION.SDK_INT < 30) throw new IllegalStateException("capture_requires_android_11");
        java.util.concurrent.CountDownLatch done=new java.util.concurrent.CountDownLatch(1);
        final byte[][] png=new byte[1][];
        final String[] error={"capture_timeout"};
        main.post(() -> {
            try {
                takeScreenshot(android.view.Display.DEFAULT_DISPLAY,getMainExecutor(),new TakeScreenshotCallback() {
                    @Override public void onSuccess(ScreenshotResult result) {
                        android.hardware.HardwareBuffer buffer=result.getHardwareBuffer();
                        android.graphics.Bitmap wrapped=null, software=null;
                        try {
                            wrapped=android.graphics.Bitmap.wrapHardwareBuffer(buffer,result.getColorSpace());
                            if(wrapped==null) { error[0]="capture_bitmap_unavailable"; return; }
                            software=wrapped.copy(android.graphics.Bitmap.Config.ARGB_8888,false);
                            java.io.ByteArrayOutputStream bytes=new java.io.ByteArrayOutputStream();
                            software.compress(android.graphics.Bitmap.CompressFormat.PNG,100,bytes);
                            png[0]=bytes.toByteArray();
                        } catch(Exception e) { error[0]="capture_encode_failed"; }
                        finally { if(software!=null)software.recycle();if(wrapped!=null)wrapped.recycle();buffer.close();done.countDown(); }
                    }
                    @Override public void onFailure(int code) { error[0]="capture_error_"+code; done.countDown(); }
                });
            } catch(Exception e) { error[0]="capture_not_available";done.countDown(); }
        });
        done.await(8,java.util.concurrent.TimeUnit.SECONDS);
        if(png[0]==null)throw new IllegalStateException(error[0]);
        return png[0];
    }

    void swipe(JSONObject args) throws Exception {
        int x1=args.getInt("x1"),y1=args.getInt("y1"),x2=args.getInt("x2"),y2=args.getInt("y2");
        int duration=args.optInt("duration",400);
        if(duration<100 || duration>2000)throw new IllegalArgumentException("invalid gesture duration");
        pathGesture(x1,y1,x2,y2,duration);
    }

    void longPress(JSONObject args) throws Exception {
        int duration=args.optInt("duration",700);
        if(duration<500 || duration>2000)throw new IllegalArgumentException("invalid gesture duration");
        pathGesture(args.getInt("x"),args.getInt("y"),args.getInt("x"),args.getInt("y"),duration);
    }

    private void pathGesture(int x1,int y1,int x2,int y2,int duration) throws Exception {
        android.util.DisplayMetrics m=getResources().getDisplayMetrics();
        if(x1<0||y1<0||x2<0||y2<0||x1>=m.widthPixels||x2>=m.widthPixels||y1>=m.heightPixels||y2>=m.heightPixels)
            throw new IllegalArgumentException("gesture outside screen");
        Path path=new Path();path.moveTo(x1,y1);if(x1!=x2||y1!=y2)path.lineTo(x2,y2);
        java.util.concurrent.CountDownLatch done=new java.util.concurrent.CountDownLatch(1);
        final boolean[] ok={false};
        main.post(() -> {
            boolean accepted=dispatchGesture(new GestureDescription.Builder().addStroke(new GestureDescription.StrokeDescription(path,0,duration)).build(),new GestureResultCallback(){
                @Override public void onCompleted(GestureDescription g){ok[0]=true;done.countDown();}
                @Override public void onCancelled(GestureDescription g){done.countDown();}
            },main);
            if(!accepted)done.countDown();
        });
        if(!done.await(duration+2500,java.util.concurrent.TimeUnit.MILLISECONDS)||!ok[0])throw new IllegalStateException("gesture_not_completed");
    }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) {}
    @Override public void onInterrupt() {}
    @Override public boolean onUnbind(Intent intent) { instance = null; return super.onUnbind(intent); }

    private static String cut(CharSequence s, int n) {
        if (s == null) return "";
        String t = s.toString().replace('\n', ' ').trim();
        return t.length() > n ? t.substring(0, n) : t;
    }

    /** The visible, meaningful elements of the active window, numbered for later steps. */
    synchronized JSONObject screen() throws JSONException {
        nodes.clear();
        AccessibilityNodeInfo root = getRootInActiveWindow();
        JSONObject out = new JSONObject().put("package", root == null ? "" : cut(root.getPackageName(), 80));
        JSONArray list = new JSONArray();
        if (root != null) walk(root, list);
        Rect screen = new Rect();
        if (root != null) root.getBoundsInScreen(screen);
        return out.put("w", screen.width()).put("h", screen.height()).put("nodes", list).put("truncated", nodes.size() >= MAX_NODES);
    }

    private void walk(AccessibilityNodeInfo n, JSONArray list) throws JSONException {
        if (n == null || nodes.size() >= MAX_NODES) return;
        if (n.isVisibleToUser()) {
            String text = cut(n.getText(), 80), desc = cut(n.getContentDescription(), 80);
            boolean useful = !text.isEmpty() || !desc.isEmpty() || n.isClickable() || n.isEditable() || n.isScrollable();
            if (useful) {
                Rect b = new Rect();
                n.getBoundsInScreen(b);
                JSONObject j = new JSONObject().put("i", nodes.size());
                if (!text.isEmpty()) j.put("text", text);
                if (!desc.isEmpty()) j.put("desc", desc);
                String id = n.getViewIdResourceName();
                if (id != null) j.put("id", id.substring(id.indexOf('/') + 1));
                if (n.isClickable()) j.put("c", true);
                if (n.isEditable()) j.put("e", true);
                if (n.isScrollable()) j.put("s", true);
                j.put("b", new JSONArray().put(b.left).put(b.top).put(b.right).put(b.bottom));
                nodes.add(n);
                list.put(j);
            }
        }
        for (int k = 0; k < n.getChildCount(); k++) walk(n.getChild(k), list);
    }

    private synchronized AccessibilityNodeInfo node(int i) {
        if (i < 0 || i >= nodes.size()) throw new IllegalArgumentException("화면에 없는 번호예요. 화면을 다시 읽어 주세요.");
        AccessibilityNodeInfo n = nodes.get(i);
        n.refresh();
        return n;
    }

    void tap(JSONObject a) {
        if (a.has("i")) {
            AccessibilityNodeInfo n = node(a.optInt("i"));
            for (AccessibilityNodeInfo p = n; p != null; p = p.getParent()) {
                if (p.isClickable() && p.performAction(AccessibilityNodeInfo.ACTION_CLICK)) return;
            }
            Rect b = new Rect();
            n.getBoundsInScreen(b);
            gesture(b.centerX(), b.centerY());
        } else if (a.has("x") && a.has("y")) {
            gesture(a.optInt("x"), a.optInt("y"));
        } else {
            throw new IllegalArgumentException("누를 번호 i나 좌표 x,y가 필요해요.");
        }
    }

    private void gesture(int x, int y) {
        Path path = new Path();
        path.moveTo(x, y);
        dispatchGesture(new GestureDescription.Builder().addStroke(new GestureDescription.StrokeDescription(path, 0, 60)).build(), null, null);
    }

    void type(JSONObject a) {
        AccessibilityNodeInfo n = a.has("i") ? node(a.optInt("i")) : findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
        if (n == null) throw new IllegalArgumentException("입력칸을 찾지 못했어요. 입력칸 번호 i를 주세요.");
        Bundle args = new Bundle();
        args.putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, a.optString("text"));
        n.performAction(AccessibilityNodeInfo.ACTION_FOCUS);
        if (!n.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) throw new IllegalStateException("글자를 넣지 못했어요.");
    }

    void key(String key) {
        switch (key) {
            case "back": performGlobalAction(GLOBAL_ACTION_BACK); return;
            case "home": performGlobalAction(GLOBAL_ACTION_HOME); return;
            case "recents": performGlobalAction(GLOBAL_ACTION_RECENTS); return;
            case "enter": {
                AccessibilityNodeInfo n = findFocus(AccessibilityNodeInfo.FOCUS_INPUT);
                if (n == null || Build.VERSION.SDK_INT < 30
                        || !n.performAction(AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.getId()))
                    throw new IllegalStateException("엔터를 누를 입력칸이 없어요.");
                return;
            }
            default: throw new IllegalArgumentException("모르는 버튼이에요: " + key);
        }
    }

    void open(String app) {
        PackageManager pm = getPackageManager();
        Intent launch = pm.getLaunchIntentForPackage(app);
        if (launch == null) {
            Intent all = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
            for (ResolveInfo r : pm.queryIntentActivities(all, 0)) {
                if (String.valueOf(r.loadLabel(pm)).replace(" ", "").equalsIgnoreCase(app.replace(" ", ""))) {
                    launch = pm.getLaunchIntentForPackage(r.activityInfo.packageName);
                    break;
                }
            }
        }
        if (launch == null) throw new IllegalArgumentException("폰에서 그 앱을 찾지 못했어요: " + app);
        startActivity(launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK));
    }

    void scroll(JSONObject a) {
        AccessibilityNodeInfo n = null;
        if (a.has("i")) n = node(a.optInt("i"));
        else synchronized (this) { for (AccessibilityNodeInfo x : nodes) if (x.isScrollable()) { n = x; break; } }
        if (n == null) throw new IllegalArgumentException("스크롤할 곳을 찾지 못했어요.");
        int action = "up".equals(a.optString("dir")) ? AccessibilityNodeInfo.ACTION_SCROLL_BACKWARD : AccessibilityNodeInfo.ACTION_SCROLL_FORWARD;
        if (!n.performAction(action)) throw new IllegalStateException("더 스크롤할 수 없어요.");
    }
}
