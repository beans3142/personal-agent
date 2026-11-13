package io.daevis.app;

import android.app.Activity;
import android.app.KeyguardManager;
import android.os.Bundle;

/**
 * Invisible screen that turns the display on and asks Android to dismiss a lock screen that
 * needs no PIN (Smart Lock / Extend Unlock), or display its credential challenge.
 */
public final class WakeActivity extends Activity {
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        if (android.os.Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true);
            setTurnScreenOn(true);
        } else {
            getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                | android.view.WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON);
        }
        KeyguardManager km = getSystemService(KeyguardManager.class);
        if (km.isKeyguardLocked()) {
            km.requestDismissKeyguard(this, new KeyguardManager.KeyguardDismissCallback() {
                @Override public void onDismissSucceeded() { finish(); }
                @Override public void onDismissError() { finish(); }
                @Override public void onDismissCancelled() { finish(); }
            });
        } else {
            finish();
        }
    }
}
