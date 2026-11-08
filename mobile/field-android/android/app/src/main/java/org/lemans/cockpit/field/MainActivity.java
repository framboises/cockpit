package org.lemans.cockpit.field;

import android.content.Intent;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.WindowManager;
import androidx.core.content.pm.ShortcutInfoCompat;
import androidx.core.content.pm.ShortcutManagerCompat;
import androidx.core.graphics.drawable.IconCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {

    private static final String KEY_HOME_SHORTCUT = "home_shortcut_asked";
    private static volatile boolean inForeground;

    static boolean isInForeground() {
        return inForeground;
    }

    @Override
    public void onResume() {
        super.onResume();
        inForeground = true;
        FieldNotifications.silenceAlarms(this);
    }

    @Override
    public void onPause() {
        inForeground = false;
        super.onPause();
    }

    @Override
    public void onCreate(Bundle savedInstanceState) {
        // Avant super.onCreate : le pont expose les plugins au chargement de la page
        registerPlugin(FieldNativePlugin.class);
        super.onCreate(savedInstanceState);
        getWindow().getAttributes().layoutInDisplayCutoutMode = WindowManager.LayoutParams.LAYOUT_IN_DISPLAY_CUTOUT_MODE_SHORT_EDGES;
        hideSystemBars();
        requestHomeShortcut();
    }

    /** Une seule fois : propose l'icone de Field sur l'ecran d'accueil. */
    private void requestHomeShortcut() {
        SharedPreferences prefs = FieldApi.prefs(this);
        if (prefs.getBoolean(KEY_HOME_SHORTCUT, false) || !ShortcutManagerCompat.isRequestPinShortcutSupported(this)) return;
        prefs.edit().putBoolean(KEY_HOME_SHORTCUT, true).apply();
        Intent launch = new Intent(this, MainActivity.class).setAction(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        ShortcutInfoCompat shortcut = new ShortcutInfoCompat.Builder(this, "field")
            .setShortLabel(getString(R.string.app_name))
            .setIcon(IconCompat.createWithResource(this, R.mipmap.ic_launcher))
            .setIntent(launch)
            .build();
        ShortcutManagerCompat.requestPinShortcut(this, shortcut, null);
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        // Android reaffiche les barres apres une fenetre systeme (permission, notification)
        if (hasFocus) hideSystemBars();
    }

    /** Plein ecran complet : les barres reviennent un instant en glissant depuis le bord. */
    private void hideSystemBars() {
        WindowCompat.setDecorFitsSystemWindows(getWindow(), false);
        WindowInsetsControllerCompat controller = WindowCompat.getInsetsController(getWindow(), getWindow().getDecorView());
        controller.setSystemBarsBehavior(WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE);
        controller.hide(WindowInsetsCompat.Type.systemBars());
    }
}
