package io.daevis.app;
import android.app.Activity;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
public final class UpdateSetup extends Activity {
    private boolean opened;
    @Override protected void onCreate(Bundle b){super.onCreate(b);}
    @Override protected void onResume(){
        super.onResume();
        if(getPackageManager().canRequestPackageInstalls()){
            getSharedPreferences("daevis",MODE_PRIVATE).edit().remove("update_checked").apply();AppUpdates.check(this);finish();
        }else if(!opened){opened=true;startActivity(new Intent(android.provider.Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,Uri.parse("package:"+getPackageName())));}
        else finish();
    }
}
