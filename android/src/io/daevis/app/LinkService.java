package io.daevis.app;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.ServiceInfo;
import android.net.ConnectivityManager;
import android.net.Network;
import android.os.Build;
import android.os.IBinder;
import android.util.Log;

import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Always-on link: one long poll to the relay at a time, held up to WAIT seconds by the server.
 * Battery: the phone wakes about 15 times an hour when idle, holds no wake lock (an arriving
 * job wakes it), backs off up to 5 minutes on errors, and pauses while there is no network.
 */
public final class LinkService extends Service {
    static final String CHANNEL = "daevis_link";
    private static final int NOTE = 4200;
    private static final int WAIT = 240;  // Server holds the poll this long; carrier NATs keep idle TCP ~5 minutes.
    private static final String TAG = "DaevisLink";
    private volatile boolean running;
    private Thread loop;
    private final Object online = new Object();
    private ConnectivityManager.NetworkCallback callback;

    static void start(Context c) {
        if (!c.getSharedPreferences("daevis", MODE_PRIVATE).contains("node")) return;
        try {
            c.startForegroundService(new Intent(c, LinkService.class));
        } catch (RuntimeException e) {
            Log.w(TAG, "start", e);  // Background start limits; the 15-minute job still covers alarms.
        }
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        Notification n = notification();
        if (Build.VERSION.SDK_INT >= 34) startForeground(NOTE, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
        else startForeground(NOTE, n);
        if (loop == null || !loop.isAlive()) {
            running = true;
            watchNetwork();
            loop = new Thread(this::run, "daevis-link");
            loop.start();
        }
        return START_STICKY;
    }

    private Notification notification() {
        NotificationChannel ch = new NotificationChannel(CHANNEL, "대비스 연결", NotificationManager.IMPORTANCE_MIN);
        ch.setDescription("대비스가 이 폰에 바로 알람 등을 보낼 수 있게 연결을 유지해요");
        ch.setShowBadge(false);
        ch.setSound(null, null);
        getSystemService(NotificationManager.class).createNotificationChannel(ch);
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_IMMUTABLE);
        return new Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_notify_sync_noanim)
            .setContentTitle("대비스 연결됨")
            .setContentIntent(open)
            .setOngoing(true)
            .build();
    }

    private void watchNetwork() {
        ConnectivityManager cm = getSystemService(ConnectivityManager.class);
        callback = new ConnectivityManager.NetworkCallback() {
            @Override public void onAvailable(Network network) {
                synchronized (online) { online.notifyAll(); }  // Retry at once instead of waiting out the backoff.
            }
        };
        try { cm.registerDefaultNetworkCallback(callback); } catch (RuntimeException e) { callback = null; }
    }

    private boolean hasNetwork() {
        ConnectivityManager cm = getSystemService(ConnectivityManager.class);
        return cm.getActiveNetwork() != null;
    }

    private void pause(long millis) {
        synchronized (online) {
            try { online.wait(millis); } catch (InterruptedException e) { Thread.currentThread().interrupt(); }
        }
    }

    private void run() {
        SharedPreferences p = getSharedPreferences("daevis", MODE_PRIVATE);
        int failures = 0;
        AppUpdates.check(this);
        while (running && !Thread.currentThread().isInterrupted()) {
            if(AppUpdates.installing){pause(1000);continue;}
            if (!p.contains("node")) { stopSelf(); return; }
            if (!hasNetwork()) { pause(10 * 60 * 1000L); continue; }  // Woken early by the network callback.
            String node = p.getString("node", ""), token = p.getString("token", "");
            try {
                JSONObject reply = Relay.send("/poll", new JSONObject().put("node", node)
                    .put("actions", new JSONArray().put("phone_alarm_at").put("phone_task")).put("wait", WAIT), token, (WAIT + 30) * 1000);
                failures = 0;
                JSONObject job = reply.optJSONObject("job");
                if (job != null && "phone_task".equals(job.optString("action"))) PhoneTask.handle(this, node, token, job);
                else if (job != null) Alarms.handle(this, node, token, job);
                else AppUpdates.check(this);  // Idle long-poll completion: never interrupt a phone step to update.
            } catch (Relay.RelayException e) {
                if (e.code == 401) { Log.w(TAG, "registration revoked"); stopSelf(); return; }
                failures++;
            } catch (Exception e) {
                failures++;
            }
            if (failures > 0) pause(Logic.backoffSeconds(failures) * 1000L);
        }
    }

    @Override
    public void onDestroy() {
        running = false;
        if (loop != null) loop.interrupt();
        if (callback != null) {
            try { getSystemService(ConnectivityManager.class).unregisterNetworkCallback(callback); } catch (RuntimeException ignored) {}
        }
        super.onDestroy();
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }
}
