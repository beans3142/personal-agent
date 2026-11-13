package io.daevis.app;

public final class UnlockFlowTest {
    static class Fake implements UnlockFlow.Device {
        boolean locked=true, field=true, accepted=true, success=true, fail=false;
        int fetches, entries;
        public boolean locked() { return locked; }
        public boolean prepare() { return field; }
        public String credential() { fetches++; if(fail) throw new IllegalStateException("private"); return "example-password"; }
        public boolean enter(String value) { entries++; return accepted; }
        public void waitForUnlock() { if(success) locked=false; }
    }
    static void check(boolean value) { if(!value) throw new AssertionError(); }
    public static void main(String[] args) {
        Fake d=new Fake(); check(UnlockFlow.run(d)); check(d.entries==1 && d.fetches==1);
        d=new Fake(); d.locked=false; check(UnlockFlow.run(d)); check(d.fetches==0);
        d=new Fake(); d.field=false; check(!UnlockFlow.run(d)); check(d.fetches==0);
        d=new Fake(); d.success=false; check(!UnlockFlow.run(d)); check(d.entries==1);
        d=new Fake(); d.accepted=false; check(!UnlockFlow.run(d)); check(d.entries==1);
        d=new Fake(); d.fail=true; check(!UnlockFlow.run(d)); check(d.entries==0);
        System.out.println("unlock flow: 6 cases passed");
    }
}
