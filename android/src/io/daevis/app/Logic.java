package io.daevis.app;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Pure rules shared by the Android app; no Android classes so plain JDK tests can run them. */
public final class Logic {
    public static final String BASE = "https://game.daehoon.games/pc-power";
    private static final Pattern CODE = Pattern.compile("(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{40,64})(?![A-Za-z0-9_-])");
    private static final Pattern NODE = Pattern.compile("phone-[a-f0-9]{16}");
    private static final Pattern HEX32 = Pattern.compile("[0-9a-f]{32}");

    private Logic() {}

    /** The one-use code from Discord's "폰 등록" message, alone or inside the install command. */
    public static String registrationCode(String pasted) {
        String text = pasted == null ? "" : pasted.trim();
        Matcher quoted = Pattern.compile("-Code\\s+'([^']*)'").matcher(text);
        if (quoted.find()) text = quoted.group(1);
        List<String> found = new ArrayList<>();
        Matcher m = CODE.matcher(text);
        while (m.find()) found.add(m.group(1));
        if (found.size() != 1) throw new IllegalArgumentException("등록 코드를 찾지 못했어요. Discord에서 \"폰 등록 이름\"으로 받은 명령을 그대로 붙여 넣어 주세요.");
        return found.get(0);
    }

    public static boolean validPhone(String node) {
        return node != null && NODE.matcher(node).matches();
    }

    public static String edgeRate(int rate) {
        int r = Math.max(-50, Math.min(50, rate));
        return (r >= 0 ? "+" : "") + r + "%";
    }

    public static float speechRate(int rate) {
        return 1f + Math.max(-50, Math.min(50, rate)) / 100f;
    }

    /** Request payload: text, how it was entered (typed only), and the conversation it continues. */
    public static Map<String, String> payload(String text, String via, String thread) {
        String t = text == null ? "" : text.trim();
        if (t.isEmpty() || t.length() > 500) throw new IllegalArgumentException("요청은 1~500자로 적어 주세요.");
        Map<String, String> p = new LinkedHashMap<>();
        p.put("text", t);
        if (!"voice".equals(via)) p.put("via", via);
        if (thread != null && HEX32.matcher(thread).matches()) p.put("thread", thread);
        return p;
    }

    public static boolean isFinal(String status) {
        switch (String.valueOf(status)) {
            case "succeeded": case "failed": case "expired": case "uncertain": case "cancelled": case "unknown": case "timeout": return true;
            default: return false;
        }
    }

    public static String failure(String status) {
        switch (String.valueOf(status)) {
            case "failed": return "처리하지 못했어요.";
            case "cancelled": return "요청이 취소됐어요.";
            case "unknown": return "요청을 찾지 못했어요.";
            default: return "답을 받지 못했어요.";
        }
    }
}
