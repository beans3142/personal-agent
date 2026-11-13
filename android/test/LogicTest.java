import io.daevis.app.Logic;
import java.util.Map;

/** Plain-JDK checks for the Android app's pure logic (run by android/build.py --test). */
public class LogicTest {
    static int failures = 0;

    static void check(boolean ok, String name) {
        System.out.println((ok ? "PASS " : "FAIL ") + name);
        if (!ok) failures++;
    }

    static boolean throwsIllegal(Runnable r) {
        try { r.run(); return false; } catch (IllegalArgumentException e) { return true; }
    }

    public static void main(String[] args) {
        String code = "A".repeat(43);
        check(code.equals(Logic.registrationCode(code)), "bare code");
        check(code.equals(Logic.registrationCode("curl -fsSL https://game.daehoon.games/pc-power/phone-install.sh | bash -s -- " + code)), "phone install command");
        check(code.equals(Logic.registrationCode("& ([scriptblock]::Create(...)) -Code '" + code + "'")), "pc install command");
        check(code.equals(Logic.registrationCode("  " + code + "\n")), "whitespace");
        check(throwsIllegal(() -> Logic.registrationCode("short")), "short code rejected");
        check(throwsIllegal(() -> Logic.registrationCode("A".repeat(70))), "long code rejected");
        check(throwsIllegal(() -> Logic.registrationCode(code + " " + "B".repeat(43))), "two codes rejected");

        check(Logic.validPhone("phone-0123456789abcdef"), "phone node ok");
        long now = 1_800_000_000L;
        check(Logic.alarmProblem(now + 3600, "기상", now) == null, "future alarm ok");
        check(Logic.alarmProblem(now - 1, "기상", now) != null, "past alarm refused");
        check(Logic.alarmProblem(now + 32L * 86400, "기상", now) != null, "alarm beyond 31 days refused");
        check(Logic.alarmProblem(now + 60, "", now) != null, "empty alarm label refused");
        check(Logic.alarmProblem(now + 60, "x".repeat(61), now) != null, "long alarm label refused");
        check(Logic.alarmCode("0123456789abcdef0123456789abcdef") == Logic.alarmCode("0123456789abcdef0123456789abcdef"), "alarm code stable");
        check(Logic.alarmCode("a") != Logic.alarmCode("b"), "alarm codes differ");
        check(Logic.backoffSeconds(0) == 0, "no wait after success");
        check(Logic.backoffSeconds(1) == 15 && Logic.backoffSeconds(2) == 30 && Logic.backoffSeconds(3) == 60, "backoff doubles");
        check(Logic.backoffSeconds(6) == 300 && Logic.backoffSeconds(50) == 300, "backoff capped at 5 minutes");
        check(!Logic.validPhone("pc-0123456789abcdef"), "pc node is not a phone registration");
        check(!Logic.validPhone("../x"), "bad node");

        check("+20%".equals(Logic.edgeRate(20)) && 1.2f == Logic.speechRate(20) && 0.5f == Logic.speechRate(-99), "speech rate");

        Map<String, String> payload = Logic.payload("  안녕  ", "voice", null);
        check(payload.equals(Map.of("text", "안녕")), "voice payload has text only");
        Map<String, String> typed = Logic.payload("이어서", "typed", "a".repeat(32));
        check("typed".equals(typed.get("via")) && typed.get("thread").length() == 32, "typed follow-up payload");
        check(throwsIllegal(() -> Logic.payload("   ", "typed", null)), "empty text rejected");
        check(throwsIllegal(() -> Logic.payload("가".repeat(501), "typed", null)), "long text rejected");
        check(!Logic.payload("x", "typed", "bad").containsKey("thread"), "invalid thread dropped");

        check(Logic.isFinal("succeeded") && Logic.isFinal("expired") && !Logic.isFinal("dispatched") && !Logic.isFinal("queued"), "final states");
        check("답을 받지 못했어요.".equals(Logic.failure("expired")) && "처리하지 못했어요.".equals(Logic.failure("failed")), "failure text");

        if (failures > 0) { System.out.println(failures + " failed"); System.exit(1); }
        System.out.println("all passed");
    }
}
