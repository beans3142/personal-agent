package io.daevis.app;

import android.Manifest;
import android.app.Activity;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.speech.tts.Voice;
import android.view.View;
import android.webkit.JavascriptInterface;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * One screen: the same HUD page as the desktop app (assets/index.html) in a WebView.
 * The page calls window.pywebview.api.*; a small shim forwards those calls to {@link Host}.
 * Requests go to the always-on relay with this phone's "폰 등록" credentials.
 */
public class MainActivity extends Activity {
    private static final String SHIM =
        "(function(){if(window.__daevisShim)return;window.__daevisShim=1;var n=0,p={};" +
        "window.__daevisResolve=function(id,json){var f=p[id];delete p[id];if(f)f(JSON.parse(json));};" +
        "window.pywebview={api:new Proxy({},{get:function(_,name){return function(){var a=Array.prototype.slice.call(arguments);" +
        "return new Promise(function(res){var id=++n;p[id]=res;DaevisHost.call(id,String(name),JSON.stringify(a));});};}})};" +
        "window.dispatchEvent(new Event('pywebviewready'));})();";
    private static final int MIC = 7, NOTIFY = 8;
    static final String EXTRA_LISTEN = "io.daevis.app.LISTEN";
    private static final String CHANNEL = "answers";
    private volatile boolean visible, pageReady, listenWhenReady;
    private int notificationId = 100;
    private static final String SAMPLE = "안녕하세요, 대비스예요. 오늘 일정은 오후 세 시 회의 하나예요.";

    private WebView web;
    private SharedPreferences prefs;
    private Relay relay;
    private TextToSpeech tts;
    private final CountDownLatch ttsReady = new CountDownLatch(1);
    private SpeechRecognizer recognizer;
    private String pendingThread;
    private final ExecutorService calls = Executors.newCachedThreadPool();
    private final ExecutorService requests = Executors.newSingleThreadExecutor();  // One request at a time, like the desktop.

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().setStatusBarColor(0xFF03060B);
        getWindow().setNavigationBarColor(0xFF03060B);
        prefs = getSharedPreferences("daevis", MODE_PRIVATE);
        if (prefs.contains("node")) relay = new Relay(prefs.getString("node", ""), prefs.getString("token", ""), prefs.getString("label", ""));
        tts = new TextToSpeech(this, status -> {
            if (status == TextToSpeech.SUCCESS) tts.setLanguage(Locale.KOREAN);
            ttsReady.countDown();
        });
        tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
            @Override public void onStart(String id) { emit(event("mode").put2("mode", "speaking")); }
            @Override public void onDone(String id) { emit(event("mode").put2("mode", "idle")); }
            @Override public void onError(String id) { emit(event("mode").put2("mode", "idle")); }
        });
        web = new WebView(this);
        web.setBackgroundColor(0xFF03060B);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);  // The page caches conversation history in localStorage.
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(false);
        web.addJavascriptInterface(new Host(), "DaevisHost");
        web.setWebViewClient(new WebViewClient() {
            @Override public void onPageFinished(WebView view, String url) {
                view.evaluateJavascript(SHIM, null);
                pageReady = true;
                if (listenWhenReady) { listenWhenReady = false; view.postDelayed(() -> listen(null), 800); }
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, String url) { return true; }  // Never navigate away.
        });
        setContentView(web);
        web.loadUrl("file:///android_asset/index.html");
        NotificationChannel channel = new NotificationChannel(CHANNEL, "대비스 답", NotificationManager.IMPORTANCE_HIGH);
        channel.setDescription("앱을 보고 있지 않을 때 온 답");
        getSystemService(NotificationManager.class).createNotificationChannel(channel);
        handleLaunch(getIntent());
    }

    /** Picks up alarms queued by Daevis now, and keeps a background check running. */
    private void syncAlarms() {
        if (relay == null) return;
        askNotifications();  // Alarms ring through a notification.
        Alarms.channel(this);
        Alarms.schedulePolling(this);
        calls.execute(() -> Alarms.sync(this));
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleLaunch(intent);
    }

    /** Assistant gesture (ACTION_ASSIST), Quick Settings tile and the "말하기" shortcut open straight into listening. */
    private void handleLaunch(Intent intent) {
        if (intent == null || relay == null) return;
        boolean talk = Intent.ACTION_ASSIST.equals(intent.getAction()) || Intent.ACTION_VOICE_COMMAND.equals(intent.getAction())
            || intent.getExtras() != null && "true".equals(String.valueOf(intent.getExtras().get(EXTRA_LISTEN)));  // Shortcut XML extras are strings.
        if (!talk) return;
        intent.removeExtra(EXTRA_LISTEN);
        if (pageReady) web.postDelayed(() -> listen(null), 300);
        else listenWhenReady = true;
    }

    @Override protected void onResume() { super.onResume(); visible = true; syncAlarms(); }
    @Override protected void onPause() { visible = false; super.onPause(); }

    /** When the answer arrives while the app is in the background, show it as a notification. */
    void notifyAnswer(String question, String answer, boolean ok) {
        if (visible) return;
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return;
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE | PendingIntent.FLAG_UPDATE_CURRENT);
        Notification n = new Notification.Builder(this, CHANNEL)
            .setSmallIcon(R.mipmap.ic_launcher)
            .setContentTitle(ok ? "대비스" : "대비스 · 처리하지 못했어요")
            .setContentText(answer)
            .setStyle(new Notification.BigTextStyle().bigText(answer).setSummaryText(question))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build();
        getSystemService(NotificationManager.class).notify(notificationId++, n);
    }

    /** Ask once for notification permission (Android 13+), the first time a request is sent. */
    void askNotifications() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                && !prefs.getBoolean("asked_notifications", false)) {
            prefs.edit().putBoolean("asked_notifications", true).apply();
            runOnUiThread(() -> requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, NOTIFY));
        }
    }

    @Override
    protected void onDestroy() {
        if (recognizer != null) recognizer.destroy();
        tts.shutdown();
        web.destroy();
        super.onDestroy();
    }

    /* ---------- events to the page ---------- */

    static final class Event extends JSONObject {
        Event put2(String k, Object v) { try { put(k, v); } catch (JSONException e) { throw new IllegalStateException(e); } return this; }
    }

    static Event event(String kind) { return new Event().put2("kind", kind); }

    void emit(JSONObject e) {
        String js = "window.daevis && window.daevis.batch([" + e + "])";
        runOnUiThread(() -> web.evaluateJavascript(js, null));
    }

    /* ---------- settings ---------- */

    JSONObject settings() throws JSONException {
        return new JSONObject().put("voice", prefs.getString("voice", "")).put("rate", prefs.getInt("rate", 0))
            .put("speak_typed", prefs.getBoolean("speak_typed", true)).put("listen", false).put("autostart", false);
    }

    JSONArray voices() throws JSONException {
        JSONArray list = new JSONArray();
        try { ttsReady.await(3, TimeUnit.SECONDS); } catch (InterruptedException ignored) {}
        List<Voice> korean = new ArrayList<>();
        try {
            for (Voice v : tts.getVoices()) if ("ko".equals(v.getLocale().getLanguage())) korean.add(v);
        } catch (RuntimeException ignored) {}
        korean.sort((a, b) -> a.getName().compareTo(b.getName()));
        int i = 1;
        for (Voice v : korean) {
            boolean online = v.isNetworkConnectionRequired();
            list.put(new JSONObject().put("id", v.getName()).put("name", "목소리 " + i++)
                .put("detail", tts.getDefaultEngine() + (online ? " · 온라인" : " · 기기 안") + (v.getQuality() >= Voice.QUALITY_HIGH ? " · 고음질" : ""))
                .put("online", online));
        }
        if (list.length() == 0) list.put(new JSONObject().put("id", "").put("name", "기기 기본 목소리").put("detail", "한국어 TTS 엔진").put("online", false));
        return list;
    }

    void say(String text, String voiceName, int rate) {
        if (text == null || text.trim().isEmpty()) return;
        try { ttsReady.await(3, TimeUnit.SECONDS); } catch (InterruptedException ignored) {}
        if (voiceName != null && !voiceName.isEmpty()) {
            try { for (Voice v : tts.getVoices()) if (v.getName().equals(voiceName)) { tts.setVoice(v); break; } } catch (RuntimeException ignored) {}
        }
        tts.setSpeechRate(Logic.speechRate(rate));
        tts.speak(text, TextToSpeech.QUEUE_FLUSH, null, "daevis-" + System.nanoTime());
    }

    void say(String text) { say(text, prefs.getString("voice", ""), prefs.getInt("rate", 0)); }

    /* ---------- one request, followed to its answer ---------- */

    void request(String text, String via, String thread) {
        requests.execute(() -> {
            boolean speak = "voice".equals(via) || prefs.getBoolean("speak_typed", true);
            String job;
            try {
                job = relay.submit(text, via, thread);
            } catch (IllegalArgumentException e) {
                emit(event("answer").put2("job", JSONObject.NULL).put2("ok", false).put2("text", e.getMessage()));
                return;
            } catch (Exception e) {
                emit(event("answer").put2("job", JSONObject.NULL).put2("ok", false).put2("text", "서버에 연결하지 못했어요. 인터넷 연결을 확인해 주세요."));
                if (speak) say("서버에 연결하지 못했어요.");
                return;
            }
            emit(event("started").put2("job", job).put2("text", text).put2("via", via).put2("thread", thread != null ? thread : job));
            String last = null, approval = null;
            boolean announced = false;
            JSONObject finalState = null;
            int errors = 0;
            for (int i = 0; i < 320 && finalState == null; i++) {
                try {
                    JSONObject st = relay.status(job);
                    errors = 0;
                    String seen = st.optString("status") + st.optString("result");
                    if (!seen.equals(last)) {
                        last = seen;
                        JSONObject r = st.optJSONObject("result");
                        if (r != null) {
                            if (!r.optString("say").isEmpty() && !announced) {
                                announced = true;
                                emit(event("step").put2("job", job).put2("text", r.optString("say")));
                                if (speak) say(r.optString("say"));
                            }
                            if (!r.optString("step").isEmpty())
                                emit(event("step").put2("job", job).put2("text", r.optString("step")).put2("steps", r.optJSONArray("steps") != null ? r.optJSONArray("steps") : new JSONArray()));
                            JSONObject a = r.optJSONObject("approval");
                            if (a != null && !a.optString("id").equals(approval)) {
                                approval = a.optString("id");
                                emit(event("approval").put2("job", job).put2("approval", a));
                            } else if (a == null && approval != null) {
                                approval = null;
                                emit(event("approval_closed").put2("job", job));
                            }
                        }
                    }
                    if (Logic.isFinal(st.optString("status"))) { finalState = st; break; }
                } catch (Exception e) {
                    if (++errors >= 10) break;  // Short network drops are retried; the receipt is still there.
                }
                try { Thread.sleep(1000); } catch (InterruptedException e) { return; }
            }
            if (approval != null) emit(event("approval_closed").put2("job", job));
            String status = finalState == null ? "timeout" : finalState.optString("status");
            JSONObject r = finalState == null ? null : finalState.optJSONObject("result");
            boolean ok = "succeeded".equals(status);
            String reply = r != null ? r.optString("reply") : "";
            String shortText = !reply.isEmpty() ? reply : ok ? "처리했어요." : Logic.failure(status);
            String full = r != null && !r.optString("text").isEmpty() ? r.optString("text") : shortText;
            emit(event("answer").put2("job", job).put2("ok", ok).put2("text", full));
            if (speak) say(shortText);
            notifyAnswer(text, shortText, ok);
        });
    }

    /* ---------- push-to-talk ---------- */

    void listen(String thread) {
        runOnUiThread(() -> {
            if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
                pendingThread = thread;
                requestPermissions(new String[]{Manifest.permission.RECORD_AUDIO}, MIC);
                return;
            }
            if (!SpeechRecognizer.isRecognitionAvailable(this)) {
                emit(event("toast").put2("text", "이 기기에서 음성 인식을 쓸 수 없어요."));
                return;
            }
            tts.stop();
            if (recognizer == null) recognizer = SpeechRecognizer.createSpeechRecognizer(this);
            recognizer.setRecognitionListener(new RecognitionListener() {
                @Override public void onReadyForSpeech(Bundle b) { emit(event("listening").put2("on", true)); }
                @Override public void onBeginningOfSpeech() {}
                @Override public void onRmsChanged(float db) { emit(event("level").put2("value", Math.max(0, (db + 2) / 400.0))); }
                @Override public void onBufferReceived(byte[] buffer) {}
                @Override public void onEndOfSpeech() {}
                @Override public void onError(int error) {
                    emit(event("listening").put2("on", false));
                    emit(event("toast").put2("text", error == SpeechRecognizer.ERROR_NO_MATCH || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT ? "말을 알아듣지 못했어요. 다시 눌러 말해 주세요." : "음성 인식이 잠시 안 돼요."));
                }
                @Override public void onResults(Bundle results) {
                    emit(event("listening").put2("on", false));
                    ArrayList<String> heard = results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                    String text = heard == null || heard.isEmpty() ? "" : heard.get(0).trim();
                    emit(event("heard").put2("text", ""));
                    if (!text.isEmpty()) request(text, "voice", thread);
                }
                @Override public void onPartialResults(Bundle partial) {
                    ArrayList<String> heard = partial.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                    if (heard != null && !heard.isEmpty()) emit(event("heard").put2("text", heard.get(0)));
                }
                @Override public void onEvent(int type, Bundle b) {}
            });
            Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "ko-KR");
            intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
            recognizer.startListening(intent);
        });
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        if (code == MIC && results.length > 0 && results[0] == PackageManager.PERMISSION_GRANTED) listen(pendingThread);
        else if (code == MIC) emit(event("toast").put2("text", "마이크 권한이 있어야 말로 요청할 수 있어요."));
    }

    /* ---------- calls from the page ---------- */

    final class Host {
        @JavascriptInterface
        public void call(int id, String name, String argsJson) {
            calls.execute(() -> {
                Object result;
                try {
                    result = dispatch(name, new JSONArray(argsJson));
                } catch (Exception e) {
                    result = errorReply(e);
                }
                String js = "window.__daevisResolve(" + id + "," + JSONObject.quote(String.valueOf(result)) + ")";
                runOnUiThread(() -> web.evaluateJavascript(js, null));
            });
        }
    }

    static JSONObject errorReply(Exception e) {
        Event r = new Event().put2("ok", false).put2("error", e instanceof IllegalArgumentException ? e.getMessage() : "서버에 연결하지 못했어요.");
        r.put2("items", new JSONArray()).put2("more", false);
        return r;
    }

    Object dispatch(String name, JSONArray a) throws Exception {
        switch (name) {
            case "boot":
                return new JSONObject().put("registered", relay != null).put("pc", relay != null ? relay.label : "")
                    .put("settings", settings()).put("voices", voices()).put("status", "")
                    .put("features", new JSONObject().put("wake", false).put("autostart", false).put("window", false).put("push", true));
            case "register": {
                JSONObject reply = Relay.enroll(a.optString(0));
                prefs.edit().putString("node", reply.getString("node")).putString("token", reply.getString("token"))
                    .putString("label", reply.optString("label")).apply();
                relay = new Relay(reply.getString("node"), reply.getString("token"), reply.optString("label"));
                syncAlarms();
                return new JSONObject().put("ok", true);
            }
            case "send": {
                String text = a.optString(0, "").trim();
                if (text.isEmpty() || relay == null) return new JSONObject().put("ok", false).put("error", "보낼 내용을 적어 주세요.");
                askNotifications();
                request(text, "typed", a.isNull(1) ? null : a.optString(1));
                return new JSONObject().put("ok", true).put("queued", false);
            }
            case "history":
                try {
                    return relay.history(a.isNull(0) ? null : a.optDouble(0), a.isNull(1) ? null : a.optString(1));
                } catch (Exception e) {
                    return new JSONObject().put("error", "기록을 불러오지 못했어요.").put("items", new JSONArray()).put("more", false);
                }
            case "answer_payment": {
                String value = a.optString(2);
                if (!"allow".equals(value) && !"deny".equals(value)) return new JSONObject().put("ok", false);
                try {
                    relay.approve(a.optString(0), a.optString(1), value);
                    return new JSONObject().put("ok", true);
                } catch (Exception e) {
                    return new JSONObject().put("ok", false).put("error", "결제 응답을 보내지 못했어요. Discord 버튼으로 답해 주세요.");
                }
            }
            case "set_setting": {
                String key = a.optString(0);
                SharedPreferences.Editor edit = prefs.edit();
                if ("voice".equals(key) && a.opt(1) instanceof String) edit.putString("voice", a.optString(1));
                else if ("rate".equals(key) && a.opt(1) instanceof Number && Math.abs(a.optInt(1)) <= 50) edit.putInt("rate", a.optInt(1));
                else if ("speak_typed".equals(key) && a.opt(1) instanceof Boolean) edit.putBoolean("speak_typed", a.optBoolean(1));
                else return new JSONObject().put("ok", false);
                edit.apply();
                return new JSONObject().put("ok", true);
            }
            case "preview":
                say(SAMPLE, a.optString(0), a.optInt(1));
                return new JSONObject().put("ok", true);
            case "listen":
                if (relay == null) return new JSONObject().put("ok", false);
                listen(a.isNull(0) ? null : a.optString(0));
                return new JSONObject().put("ok", true);
            case "stop_speaking":
                tts.stop();
                return new JSONObject().put("ok", true);
            case "minimize": case "hide":
                runOnUiThread(() -> moveTaskToBack(true));
                return JSONObject.NULL;
            case "quit":
                runOnUiThread(this::finish);
                return JSONObject.NULL;
            default:
                return new JSONObject().put("ok", false).put("error", "unknown call");
        }
    }

    @Override
    public void onBackPressed() {
        web.evaluateJavascript("(function(){var d=document.getElementById('drawer'),a=document.getElementById('app');" +
            "if(d&&d.classList.contains('open')){d.classList.remove('open');return 1}if(a&&a.classList.contains('nav-open')){a.classList.remove('nav-open');return 1}" +
            "var p=document.getElementById('palette');if(p&&!p.classList.contains('hidden')){p.classList.add('hidden');return 1}return 0})()",
            handled -> { if (!"1".equals(handled)) moveTaskToBack(true); });
    }
}
