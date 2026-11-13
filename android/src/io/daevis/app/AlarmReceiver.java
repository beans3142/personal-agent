package io.daevis.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** Alarm time reached, alarm dismissed, or the phone rebooted (alarms must be set again). */
public final class AlarmReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context c, Intent intent) {
        String action = intent.getAction();
        if (Alarms.FIRE.equals(action)) {
            Alarms.ring(c, intent.getStringExtra("id"), intent.getStringExtra("text"));
        } else if (Alarms.STOP.equals(action)) {
            Alarms.stop(c, intent.getStringExtra("id"));
        } else if (Intent.ACTION_BOOT_COMPLETED.equals(action) || Intent.ACTION_MY_PACKAGE_REPLACED.equals(action)) {
            Alarms.restore(c);
            try { Alarms.schedulePolling(c); } catch (RuntimeException e) { android.util.Log.w("DaevisAlarm", "polling", e); }
            LinkService.start(c);
        }
    }
}
