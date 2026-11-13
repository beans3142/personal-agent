package io.daevis.app;
final class UnlockPolicy {
    static boolean field(String pkg, String id, boolean visible, boolean password, boolean editable, boolean setText) {
        if (!"com.android.systemui".equals(pkg) || !visible || !setText) return false;
        String name=id==null ? "" : id.substring(id.indexOf('/')+1);
        return (password && editable) || "passwordEntry".equals(name) || "password_entry".equals(name);
    }
}
