package io.daevis.app;

import android.app.AlarmManager;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.job.JobInfo;
import android.app.job.JobScheduler;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.media.AudioAttributes;
import android.media.RingtoneManager;
import android.net.Uri;
import android.os.Build;
import android.util.Log;

import java.util.Iterator;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Phone alarms queued by Daevis: fetch `phone_alarm_at` jobs from the relay, register one exact
 * alarm per job, ring with the alarm sound until dismissed, and restore them after a reboot.
 */
final class Alarms {
    static final String CHANNEL = "daevis_alarm";
    static final String FIRE = "io.daevis.app.ALARM_FIRE";
    static final String STOP = "io.daevis.app.ALARM_STOP";
    private static final int POLL_JOB = 4100;
    private static final String TAG = "DaevisAlarm";

    private Alarms() {}

    private static SharedPreferences prefs(Context c) {
        return c.getSharedPreferences("daevis", Context.MODE_PRIVATE);
    }

    /** Background check every ~15 minutes (Android's minimum), kept across reboots. */
    static void schedulePolling(Context c) {
        JobScheduler js = c.getSystemService(JobScheduler.class);
        if (js.getPendingJob(POLL_JOB) != null) return;
        js.schedule(new JobInfo.Builder(POLL_JOB, new ComponentName(c, PollJob.class))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setPeriodic(15 * 60 * 1000L)
            .setPersisted(true)
            .build());
    }

    /** Takes queued alarm jobs for this phone; returns how many were registered. Network: call off the main thread. */
    static int sync(Context c) {
        SharedPreferences p = prefs(c);
        if (!p.contains("node")) return 0;
        String node = p.getString("node", ""), token = p.getString("token", "");
        int added = 0;
        for (int i = 0; i < 5; i++) {
            JSONObject job;
            try {
                JSONObject reply = Relay.send("/poll", new JSONObject().put("node", node)
                    .put("actions", new JSONArray().put("phone_alarm_at")), token);
                job = reply.optJSONObject("job");
            } catch (Exception e) {
                Log.w(TAG, "poll failed: " + e.getMessage());
                return added;
            }
            if (job == null) break;
            if (handle(c, node, token, job)) added++;
        }
        return added;
    }


    /** Registers one alarm job and reports the result to the relay; true when the alarm is set. */
    static boolean handle(Context c, String node, String token, JSONObject job) {
        String id = job.optString("id");
        JSONObject payload = job.optJSONObject("payload");
        long at = payload == null ? 0 : payload.optLong("at", 0);
        String text = payload == null ? "" : payload.optString("text", "");
        String problem = "phone_alarm_at".equals(job.optString("action")) ? Logic.alarmProblem(at, text, System.currentTimeMillis() / 1000) : "not an alarm";
        String status = "failed";
        JSONObject result = new JSONObject();
        boolean ok = false;
        try {
            if (problem == null) {
                remember(c, id, at, text.trim());
                boolean exact = arm(c, id, at, text.trim());
                status = "succeeded";
                result.put("scheduled", true).put("exact", exact);
                ok = true;
            } else {
                result.put("error", problem);
            }
        } catch (Exception e) {
            status = "uncertain";
            try { result.put("error", e.getClass().getSimpleName()); } catch (JSONException ignored) {}
        }
        try {
            Relay.send("/ack", new JSONObject().put("node", node).put("id", id).put("status", status).put("result", result), token);
        } catch (Exception e) {
            Log.w(TAG, "ack failed: " + e.getMessage());
        }
        return ok;
    }

    private static void remember(Context c, String id, long at, String text) throws JSONException {
        JSONObject all = stored(c);
        all.put(id, new JSONObject().put("at", at).put("text", text));
        prefs(c).edit().putString("alarms", all.toString()).apply();
    }

    private static JSONObject stored(Context c) {
        try { return new JSONObject(prefs(c).getString("alarms", "{}")); } catch (JSONException e) { return new JSONObject(); }
    }

    private static PendingIntent fireIntent(Context c, String id, String text) {
        Intent i = new Intent(c, AlarmReceiver.class).setAction(FIRE).putExtra("id", id).putExtra("text", text);
        return PendingIntent.getBroadcast(c, Logic.alarmCode(id), i, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    /** Registers the alarm; true when Android allows an exact alarm-clock entry. */
    static boolean arm(Context c, String id, long atSeconds, String text) {
        AlarmManager am = c.getSystemService(AlarmManager.class);
        PendingIntent fire = fireIntent(c, id, text);
        long when = atSeconds * 1000L;
        if (Build.VERSION.SDK_INT < 31 || am.canScheduleExactAlarms()) {
            PendingIntent show = PendingIntent.getActivity(c, 0, new Intent(c, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
            am.setAlarmClock(new AlarmManager.AlarmClockInfo(when, show), fire);
            return true;
        }
        am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, when, fire);
        return false;
    }

    /** After a reboot alarms are gone from AlarmManager; set the ones still ahead again. */
    static void restore(Context c) {
        JSONObject all = stored(c);
        long now = System.currentTimeMillis() / 1000;
        for (Iterator<String> it = all.keys(); it.hasNext(); ) {
            String id = it.next();
            JSONObject a = all.optJSONObject(id);
            if (a != null && a.optLong("at") > now) arm(c, id, a.optLong("at"), a.optString("text"));
        }
    }

    static void forget(Context c, String id) {
        JSONObject all = stored(c);
        all.remove(id);
        prefs(c).edit().putString("alarms", all.toString()).apply();
    }

    static void channel(Context c) {
        NotificationChannel ch = new NotificationChannel(CHANNEL, "대비스 알람", NotificationManager.IMPORTANCE_HIGH);
        ch.setDescription("대비스로 맞춘 알람");
        Uri sound = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM);
        ch.setSound(sound, new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM)
            .setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build());
        ch.enableVibration(true);
        ch.setVibrationPattern(new long[]{0, 800, 600, 800, 600});
        ch.setBypassDnd(true);
        c.getSystemService(NotificationManager.class).createNotificationChannel(ch);
    }

    /** Rings (repeating sound) until the owner taps or dismisses it. */
    static void ring(Context c, String id, String text) {
        channel(c);
        PendingIntent stop = PendingIntent.getBroadcast(c, Logic.alarmCode(id) ^ 1,
            new Intent(c, AlarmReceiver.class).setAction(STOP).putExtra("id", id), PendingIntent.FLAG_IMMUTABLE);
        PendingIntent open = PendingIntent.getActivity(c, Logic.alarmCode(id) ^ 2, new Intent(c, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        Notification n = new Notification.Builder(c, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
            .setContentTitle("⏰ " + (text == null || text.isEmpty() ? "대비스 알람" : text))
            .setContentText("눌러서 끄기")
            .setCategory(Notification.CATEGORY_ALARM)
            .setContentIntent(open)
            .setAutoCancel(true)
            .setFullScreenIntent(open, true)
            .addAction(new Notification.Action.Builder(null, "끄기", stop).build())
            .build();
        n.flags |= Notification.FLAG_INSISTENT;
        c.getSystemService(NotificationManager.class).notify(Logic.alarmCode(id), n);
        forget(c, id);
    }

    static void stop(Context c, String id) {
        c.getSystemService(NotificationManager.class).cancel(Logic.alarmCode(id));
    }
}
