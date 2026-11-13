package io.daevis.app;

import android.app.*;
import android.content.*;
import android.content.pm.*;
import android.os.Build;
import java.io.*;
import java.net.*;
import java.security.MessageDigest;
import java.util.Arrays;
import org.json.JSONObject;

/** Self-updates only this signed package. Android may still require one-time installation consent. */
final class AppUpdates {
    static final String CHANNEL="daevis_updates";
    static final int NOTE=4300;
    private static boolean running;
    static volatile boolean installing;

    static void check(Context context) {
        synchronized(AppUpdates.class){if(running)return;running=true;}
        Context c=context.getApplicationContext();
        new Thread(() -> {
            try { perform(c); }
            catch(Exception ignored) { /* A failed update must not stop phone control or expose credentials. */ }
            finally { synchronized(AppUpdates.class){running=false;} }
        },"daevis-update").start();
    }

    private static void perform(Context c) throws Exception {
        android.content.SharedPreferences prefs=c.getSharedPreferences("daevis",Context.MODE_PRIVATE);
        long now=System.currentTimeMillis();
        if(now-prefs.getLong("update_checked",0)<15*60*1000L || now-prefs.getLong("phone_task_at",0)<120000)return;
        if(!prefs.contains("node"))return;
        prefs.edit().putLong("update_checked",now).apply();
        String node=prefs.getString("node",""),token=prefs.getString("token","");
        JSONObject meta=Relay.send("/phone/update/check",new JSONObject().put("node",node),token);
        long version=meta.getLong("version");
        PackageManager pm=c.getPackageManager();
        PackageInfo own=pm.getPackageInfo(c.getPackageName(),PackageManager.GET_SIGNATURES);
        long local=Build.VERSION.SDK_INT>=28 ? own.getLongVersionCode() : own.versionCode;
        if(!UpdatePolicy.newer(version,local))return;
        if(prefs.getLong("update_pending",0)==version && now-prefs.getLong("update_pending_at",0)<6*60*60*1000L)return;
        if(!pm.canRequestPackageInstalls()){
            Intent setup=new Intent(c,UpdateSetup.class);
            notify(c,"대비스 자동 업데이트", "한 번만 앱 업데이트 설치를 허용해 주세요",setup);
            return;
        }
        long size=meta.getLong("size");if(size<=0||size>20000000)return;
        File apk=new File(c.getCacheDir(),"daevis-update.apk");
        download(node,token,version,size,apk);
        MessageDigest sha=MessageDigest.getInstance("SHA-256");
        try(InputStream in=new FileInputStream(apk)){byte[] b=new byte[8192];int n;while((n=in.read(b))>0)sha.update(b,0,n);}
        StringBuilder hex=new StringBuilder();for(byte b:sha.digest())hex.append(String.format("%02x",b&255));
        if(!hex.toString().equals(meta.getString("sha256")))throw new IOException("invalid update digest");
        PackageInfo candidate=pm.getPackageArchiveInfo(apk.getAbsolutePath(),PackageManager.GET_SIGNATURES);
        boolean same=candidate!=null && own.signatures!=null && candidate.signatures!=null && Arrays.equals(own.signatures,candidate.signatures);
        long actual=candidate==null?0:Build.VERSION.SDK_INT>=28?candidate.getLongVersionCode():candidate.versionCode;
        if(candidate==null||!UpdatePolicy.valid(candidate.packageName,c.getPackageName(),actual,version,same))throw new IOException("invalid update identity");
        PackageInstaller installer=pm.getPackageInstaller();
        PackageInstaller.SessionParams params=new PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL);
        params.setAppPackageName(c.getPackageName());params.setSize(apk.length());
        if(Build.VERSION.SDK_INT>=31)params.setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED);
        int id=installer.createSession(params);
        try(PackageInstaller.Session session=installer.openSession(id)){
            try(InputStream input=new FileInputStream(apk);OutputStream out=session.openWrite("base.apk",0,apk.length())){
                byte[] buf=new byte[8192];int n;while((n=input.read(buf))>0)out.write(buf,0,n);session.fsync(out);
            }
            Intent callback=new Intent(c,UpdateReceiver.class).setAction("io.daevis.app.UPDATE_RESULT");
            int flags=PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT>=31?PendingIntent.FLAG_MUTABLE:0);
            prefs.edit().putLong("update_pending",version).putLong("update_pending_at",now).apply();
            // A new job may have arrived while the APK downloaded. Defer instead of replacing the running app.
            if(System.currentTimeMillis()-prefs.getLong("phone_task_at",0)<120000){installer.abandonSession(id);prefs.edit().remove("update_pending").apply();return;}
            installing=true;
            session.commit(PendingIntent.getBroadcast(c,id,callback,flags).getIntentSender());
        }catch(Exception e){installing=false;installer.abandonSession(id);throw e;}
        finally{apk.delete();}
    }

    private static void download(String node,String token,long version,long expected,File target) throws Exception {
        HttpURLConnection conn=(HttpURLConnection)new URL(Logic.BASE+"/phone/update/apk").openConnection();
        conn.setInstanceFollowRedirects(false);conn.setConnectTimeout(15000);conn.setReadTimeout(60000);
        conn.setRequestMethod("POST");conn.setDoOutput(true);conn.setRequestProperty("Authorization","Bearer "+token);conn.setRequestProperty("Content-Type","application/json");
        try {
            try(OutputStream out=conn.getOutputStream()){out.write(new JSONObject().put("node",node).put("version",version).toString().getBytes(java.nio.charset.StandardCharsets.UTF_8));}
            if(conn.getResponseCode()!=200)throw new IOException("update unavailable");
            long total=0;
            try(InputStream input=conn.getInputStream();OutputStream out=new FileOutputStream(target)){
                byte[] buf=new byte[8192];int n;while((n=input.read(buf))>0){total+=n;if(total>expected)throw new IOException("oversized update");out.write(buf,0,n);}
            }
            if(total!=expected)throw new IOException("incomplete update");
        }finally{conn.disconnect();}
    }

    static void notify(Context c,String title,String text,Intent action){
        NotificationManager nm=c.getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL,"대비스 업데이트",NotificationManager.IMPORTANCE_DEFAULT));
        PendingIntent tap=PendingIntent.getActivity(c,NOTE,action,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        try{nm.notify(NOTE,new Notification.Builder(c,CHANNEL).setSmallIcon(android.R.drawable.stat_sys_download_done).setContentTitle(title).setContentText(text).setContentIntent(tap).setAutoCancel(true).build());}catch(SecurityException ignored){}
    }
}
