package io.daevis.app;
import android.app.*;
import android.content.*;
import android.content.pm.PackageInstaller;
public final class UpdateReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context c,Intent result){
        AppUpdates.installing=false;
        int status=result.getIntExtra(PackageInstaller.EXTRA_STATUS,PackageInstaller.STATUS_FAILURE);
        if(status==PackageInstaller.STATUS_PENDING_USER_ACTION){
            c.getSharedPreferences("daevis",Context.MODE_PRIVATE).edit().putString("update_state","awaiting_confirmation").apply();
            Intent confirmation=result.getParcelableExtra(Intent.EXTRA_INTENT);
            if(confirmation!=null)AppUpdates.notify(c,"대비스 업데이트","눌러서 업데이트 설치를 확인해 주세요",confirmation);
        }else{
            c.getSharedPreferences("daevis",Context.MODE_PRIVATE).edit().putString("update_state",status==PackageInstaller.STATUS_SUCCESS?"up_to_date":"failed").apply();
            c.getSharedPreferences("daevis",Context.MODE_PRIVATE).edit().remove("update_pending").apply();
            if(status==PackageInstaller.STATUS_SUCCESS){
                c.getSystemService(NotificationManager.class).cancel(AppUpdates.NOTE);LinkService.start(c);
            }
        }
    }
}
