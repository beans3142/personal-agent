package io.daevis.app;

/** One attempt per request. The platform adapter never returns password-screen contents. */
final class UnlockFlow {
    interface Device {
        default void hold() throws Exception {}
        default void release() {}
        boolean locked();
        boolean prepare() throws Exception;
        String credential() throws Exception;
        boolean enter(String password) throws Exception;
        void waitForUnlock() throws Exception;
    }
    static boolean run(Device device) {
        try {
            device.hold();
            if (!device.locked()) return true;
            if (!device.prepare()) return false;
            String password = device.credential();
            if (password == null || password.isEmpty() || !device.enter(password)) return false;
            device.waitForUnlock();
            return !device.locked();
        } catch (Exception ignored) {
            return false;
        } finally {
            device.release();
        }
    }
}
