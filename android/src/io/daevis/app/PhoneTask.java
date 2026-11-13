package io.daevis.app;

import android.content.Context;
import android.util.Log;

import java.nio.charset.StandardCharsets;

import org.json.JSONObject;

/** Runs one phone_task job from the relay and reports the result (the screen goes up as a file). */
final class PhoneTask {
    private PhoneTask() {}

    static void handle(Context c, String node, String token, JSONObject job) {
        String id = job.optString("id");
        JSONObject payload = job.optJSONObject("payload");
        String status = "failed";
        JSONObject result = new JSONObject();
        try {
            DaevisAccess a = DaevisAccess.instance;
            if (a == null) throw new IllegalStateException("폰에서 대비스 접근성 권한이 꺼져 있어요. 설정 > 접근성 > 설치된 앱 > 대비스를 켜 주세요.");
            String op = payload == null ? "" : payload.optString("op");
            JSONObject args = payload == null || payload.optJSONObject("args") == null ? new JSONObject() : payload.optJSONObject("args");
            JSONObject out;
            if (!a.ensureAwake() && !a.unlock(node, token, id)) {
                // Never upload the password field or a partially entered credential.
                out = new JSONObject().put("locked", true).put("nodes", new org.json.JSONArray());
            } else if ("screen".equals(op)) {
                out = a.screen();
            } else {
                switch (op) {
                    case "tap": a.tap(args); break;
                    case "type": a.type(args); break;
                    case "key": a.key(args.optString("key")); break;
                    case "open": a.open(args.optString("app")); break;
                    case "scroll": a.scroll(args); break;
                    default: throw new IllegalArgumentException("모르는 동작이에요: " + op);
                }
                Thread.sleep("open".equals(op) ? 1800 : 900);  // Let the app react before reading the screen.
                out = new JSONObject().put("ok", true).put("screen", a.screen());
            }
            a.scheduleRelock();
            String file = Relay.upload(node, token, "phone-" + op + ".json", out.toString().getBytes(StandardCharsets.UTF_8));
            result.put("data_file", file);
            status = "succeeded";
        } catch (Exception e) {
            try { result.put("error", e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage()); } catch (Exception ignored) {}
        }
        try {
            Relay.send("/ack", new JSONObject().put("node", node).put("id", id).put("status", status).put("result", result), token);
        } catch (Exception e) {
            Log.w("DaevisTask", "ack failed: " + e.getMessage());
        }
    }
}
