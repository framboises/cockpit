package org.lemans.cockpit.field;

import android.Manifest;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.PowerManager;
import android.provider.Settings;
import android.util.Log;
import androidx.core.app.ActivityCompat;
import androidx.core.content.ContextCompat;
import androidx.lifecycle.Lifecycle;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;
import com.google.firebase.FirebaseApp;
import com.google.firebase.messaging.FirebaseMessaging;

/**
 * Pont expose a field.js. Capacitor ne l'ouvre qu'a l'origine du serveur configure
 * (getAllowedOriginRules) et envoie toute autre adresse vers le navigateur externe.
 */
@CapacitorPlugin(
    name = "FieldNative",
    permissions = {
        @Permission(alias = "location", strings = { Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION })
    }
)
public class FieldNativePlugin extends Plugin {

    private static final String TAG = "FieldNative";
    private static final String KEY_BATTERY_ASKED = "battery_asked";
    private static final int REQUEST_NOTIFICATIONS = 4211;

    private final FieldEvents.Listener eventListener = this::notifyListeners;

    @Override
    public void load() {
        Uri server = Uri.parse(getBridge().getServerUrl());
        String origin = server.getScheme() + "://" + server.getEncodedAuthority();
        FieldApi.saveOrigin(getContext(), origin);
        FieldNotifications.createChannels(getContext());
        FieldEvents.setListener(eventListener);
        handleIntent(getActivity().getIntent());
    }

    @Override
    protected void handleOnNewIntent(Intent intent) {
        super.handleOnNewIntent(intent);
        handleIntent(intent);
    }

    @Override
    protected void handleOnDestroy() {
        FieldEvents.clearListener(eventListener);
        super.handleOnDestroy();
    }

    private void handleIntent(Intent intent) {
        if (intent == null || !intent.hasExtra(FieldNotifications.EXTRA_URL)) return;
        JSObject event = new JSObject();
        event.put("url", intent.getStringExtra(FieldNotifications.EXTRA_URL));
        intent.removeExtra(FieldNotifications.EXTRA_URL);
        // Demarrage a froid : conserve jusqu'a l'ecoute de la page
        notifyListeners("openUrl", event, true);
    }

    @PluginMethod
    public void startTracking(PluginCall call) {
        if (getPermissionState("location") != PermissionState.GRANTED) {
            requestPermissionForAlias("location", call, "locationPermissionResult");
            return;
        }
        startService(call);
    }

    @PermissionCallback
    private void locationPermissionResult(PluginCall call) {
        if (getPermissionState("location") == PermissionState.GRANTED) {
            startService(call);
        } else {
            call.reject("permission_refusee");
        }
    }

    private void startService(PluginCall call) {
        String profile = call.getString("profile", "normal");
        long pushMs = call.getLong("pushMs", call.getInt("pushMs", 60000).longValue());
        boolean posting = Boolean.TRUE.equals(call.getBoolean("posting", true));
        boolean sos = Boolean.TRUE.equals(call.getBoolean("sos", false));

        FieldLocationService running = FieldLocationService.running();
        if (running != null) {
            getActivity().runOnUiThread(() -> {
                running.configure(profile, pushMs, posting, sos);
                call.resolve();
            });
            return;
        }
        Intent intent = new Intent(getContext(), FieldLocationService.class)
            .putExtra(FieldLocationService.EXTRA_PROFILE, profile)
            .putExtra(FieldLocationService.EXTRA_PUSH_MS, pushMs)
            .putExtra(FieldLocationService.EXTRA_POSTING, posting)
            .putExtra(FieldLocationService.EXTRA_SOS, sos);
        try {
            ContextCompat.startForegroundService(getContext(), intent);
        } catch (RuntimeException e) {
            // Android refuse de demarrer un service de position depuis l'arriere-plan
            Log.w(TAG, "Demarrage du suivi refuse : " + e.getMessage());
            call.reject("demarrage_refuse");
            return;
        }
        requestNotificationPermission();
        requestBatteryExemption();
        call.resolve();
    }

    @PluginMethod
    public void stopTracking(PluginCall call) {
        FieldLocationService.markStopped(getContext());
        FieldLocationService running = FieldLocationService.running();
        if (running != null) {
            getActivity().runOnUiThread(() -> running.stopTracking(null));
        }
        call.resolve();
    }

    @PluginMethod
    public void setPosting(PluginCall call) {
        boolean enabled = Boolean.TRUE.equals(call.getBoolean("enabled", true));
        FieldLocationService running = FieldLocationService.running();
        if (running != null) {
            getActivity().runOnUiThread(() -> running.setPosting(enabled));
        }
        call.resolve();
    }

    /** Controle de documents : texte et codes lus sur une image JPEG (base64). */
    @PluginMethod
    public void scanImage(PluginCall call) {
        String image = call.getString("image");
        if (image == null || image.isEmpty()) {
            call.reject("image_absente");
            return;
        }
        FieldScanner.scan(image, new FieldScanner.Callback() {
            @Override
            public void done(JSObject result) {
                call.resolve(result);
            }

            @Override
            public void failed(String error) {
                call.reject(error);
            }
        });
    }

    @PluginMethod
    public void getFcmToken(PluginCall call) {
        if (FirebaseApp.getApps(getContext()).isEmpty()) {
            // google-services.json absent du build
            call.reject("firebase_non_configure");
            return;
        }
        FirebaseMessaging.getInstance().getToken().addOnCompleteListener(task -> {
            if (!task.isSuccessful() || task.getResult() == null) {
                call.reject("jeton_indisponible");
                return;
            }
            JSObject result = new JSObject();
            result.put("token", task.getResult());
            result.put("version", BuildConfig.VERSION_NAME);
            call.resolve(result);
        });
    }

    private boolean isAppInForeground() {
        return getActivity().getLifecycle().getCurrentState().isAtLeast(Lifecycle.State.RESUMED);
    }

    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return;
        if (ContextCompat.checkSelfPermission(getContext(), Manifest.permission.POST_NOTIFICATIONS) == android.content.pm.PackageManager.PERMISSION_GRANTED) return;
        ActivityCompat.requestPermissions(getActivity(), new String[] { Manifest.permission.POST_NOTIFICATIONS }, REQUEST_NOTIFICATIONS);
    }

    /** Demande une seule fois : sans cela, l'economie de batterie peut couper le suivi. */
    private void requestBatteryExemption() {
        android.content.SharedPreferences prefs = FieldApi.prefs(getContext());
        PowerManager power = getContext().getSystemService(PowerManager.class);
        if (prefs.getBoolean(KEY_BATTERY_ASKED, false) || power.isIgnoringBatteryOptimizations(getContext().getPackageName())) return;
        if (!isAppInForeground()) return;
        try {
            Intent intent = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getContext().getPackageName()));
            getActivity().startActivity(intent);
            prefs.edit().putBoolean(KEY_BATTERY_ASKED, true).apply();
        } catch (RuntimeException e) {
            Log.w(TAG, "Exclusion de batterie indisponible : " + e.getMessage());
        }
    }
}
