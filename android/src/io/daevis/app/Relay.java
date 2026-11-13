package io.daevis.app;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Map;
import java.util.UUID;

import org.json.JSONException;
import org.json.JSONObject;

/** The always-on relay, with this phone's registration (same endpoints as the desktop app). */
final class Relay {
    static final class RelayException extends IOException {
        final int code;
        RelayException(int code) { super("relay answered HTTP " + code); this.code = code; }
    }

    final String node, label;
    private final String token;

    Relay(String node, String token, String label) {
        this.node = node; this.token = token; this.label = label;
    }

    static JSONObject send(String path, JSONObject body, String token) throws IOException {
        return send(path, body, token, 25000);
    }

    static JSONObject send(String path, JSONObject body, String token, int readTimeoutMs) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(Logic.BASE + path).openConnection();
        c.setInstanceFollowRedirects(false);  // Bearer credentials never follow a redirect.
        c.setConnectTimeout(15000);
        c.setReadTimeout(readTimeoutMs);
        c.setRequestMethod("POST");
        c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "application/json");
        if (token != null) c.setRequestProperty("Authorization", "Bearer " + token);
        try (OutputStream out = c.getOutputStream()) {
            out.write(body.toString().getBytes(StandardCharsets.UTF_8));
        }
        int code = c.getResponseCode();
        if (code != 200) { c.disconnect(); throw new RelayException(code); }
        try (InputStream in = c.getInputStream()) {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[8192];
            int n;
            while ((n = in.read(chunk)) > 0) {
                buffer.write(chunk, 0, n);
                if (buffer.size() > 1_000_000) throw new IOException("oversized reply");
            }
            return new JSONObject(buffer.toString("UTF-8"));
        } catch (JSONException e) {
            throw new IOException("invalid reply", e);
        } finally {
            c.disconnect();
        }
    }

    /** Upload bytes for the main agent (screen results too big for a job receipt); returns the file id. */
    static String upload(String node, String token, String name, byte[] data) throws IOException {
        HttpURLConnection c = (HttpURLConnection) new URL(Logic.BASE + "/pc/upload").openConnection();
        c.setInstanceFollowRedirects(false);
        c.setConnectTimeout(15000);
        c.setReadTimeout(60000);
        c.setRequestMethod("POST");
        c.setDoOutput(true);
        c.setFixedLengthStreamingMode(data.length);
        c.setRequestProperty("Content-Type", "application/octet-stream");
        c.setRequestProperty("Authorization", "Bearer " + token);
        c.setRequestProperty("X-Daevis-Node", node);
        c.setRequestProperty("X-Daevis-Audience", "agent-main");
        c.setRequestProperty("X-Daevis-Name", java.net.URLEncoder.encode(name, "UTF-8"));
        try (OutputStream out = c.getOutputStream()) { out.write(data); }
        int code = c.getResponseCode();
        if (code != 200) { c.disconnect(); throw new RelayException(code); }
        try (InputStream in = c.getInputStream()) {
            ByteArrayOutputStream buffer = new ByteArrayOutputStream();
            byte[] chunk = new byte[4096];
            int n;
            while ((n = in.read(chunk)) > 0) buffer.write(chunk, 0, n);
            return new JSONObject(buffer.toString("UTF-8")).getString("id");
        } catch (JSONException e) {
            throw new IOException("invalid upload reply", e);
        } finally {
            c.disconnect();
        }
    }

    private JSONObject post(String path, JSONObject body) throws IOException {
        try {
            return send(path, body.put("node", node), token);
        } catch (JSONException e) {
            throw new IOException(e);
        }
    }

    String submit(String text, String via, String thread) throws IOException, JSONException {
        JSONObject payload = new JSONObject();
        for (Map.Entry<String, String> e : Logic.payload(text, via, thread).entrySet()) payload.put(e.getKey(), e.getValue());
        JSONObject body = new JSONObject().put("target", "voice").put("action", "voice_command").put("payload", payload)
                .put("request_key", "app-" + UUID.randomUUID().toString().replace("-", "")).put("ttl_seconds", 300);
        return post("/request", body).getString("id");
    }

    JSONObject status(String id) throws IOException, JSONException {
        return post("/request-status", new JSONObject().put("id", id));
    }

    JSONObject history(Double before, String q) throws IOException, JSONException {
        JSONObject body = new JSONObject().put("limit", 50);
        if (before != null) body.put("before", before);
        if (q != null && !q.trim().isEmpty()) body.put("q", q.trim().length() > 100 ? q.trim().substring(0, 100) : q.trim());
        return post("/history", body);
    }

    void approve(String job, String approval, String value) throws IOException, JSONException {
        post("/approve", new JSONObject().put("id", job).put("approval", approval).put("value", value));
    }

    /** New-device request: this phone asks to connect and waits for the owner to accept. */
    static JSONObject pairStart(String name) throws IOException, JSONException {
        return send("/pair/start", new JSONObject().put("kind", "phone").put("name", name == null ? "" : name), null);
    }

    static JSONObject pairPoll(String id) throws IOException, JSONException {
        return send("/pair/poll", new JSONObject().put("id", id), null);
    }

    /** As a connected phone: list or answer other devices' requests. */
    JSONObject pair(String path, String code) throws IOException, JSONException {
        JSONObject body = new JSONObject();
        if (code != null) body.put("code", code);
        return post(path, body);
    }

    /** Redeem a "폰 등록" code. Returns node, token and label. */
    static JSONObject enroll(String pasted) throws IOException, JSONException {
        JSONObject reply = send("/enroll", new JSONObject().put("code", Logic.registrationCode(pasted)), null);
        if (!Logic.validPhone(reply.optString("node")) || !Logic.BASE.equals(reply.optString("url")) || reply.optString("token").isEmpty())
            throw new IllegalArgumentException("휴대폰 등록 코드가 아니에요. Discord에서 \"폰 등록 이름\"으로 받은 코드를 써 주세요.");
        return reply;
    }
}
