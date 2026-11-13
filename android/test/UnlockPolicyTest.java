package io.daevis.app;
public final class UnlockPolicyTest {
    static void check(boolean v) { if(!v) throw new AssertionError(); }
    public static void main(String[] args) {
        check(UnlockPolicy.field("com.android.systemui", "com.android.systemui:id/passwordEntry", true, false, false, true));
        check(UnlockPolicy.field("com.android.systemui", null, true, true, true, true));
        check(!UnlockPolicy.field("com.other.app", "passwordEntry", true, true, true, true));
        check(!UnlockPolicy.field("com.android.systemui", "clock", true, false, false, true));
        check(!UnlockPolicy.field("com.android.systemui", "passwordEntry", false, true, true, true));
        check(!UnlockPolicy.field("com.android.systemui", "passwordEntry", true, true, true, false));
        check(!UnlockPolicy.field(null, null, true, true, true, true));
        System.out.println("unlock policy: 7 cases passed");
    }
}
