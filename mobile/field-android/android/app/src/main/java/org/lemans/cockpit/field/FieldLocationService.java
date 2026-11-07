package org.lemans.cockpit.field;

import android.app.Notification;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.location.Location;
import android.os.BatteryManager;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.PowerManager;
import android.util.Log;
import androidx.annotation.NonNull;
import androidx.annotation.Nullable;
import androidx.core.app.NotificationCompat;
import androidx.core.app.ServiceCompat;
import com.getcapacitor.JSObject;
import com.google.android.gms.location.FusedLocationProviderClient;
import com.google.android.gms.location.LocationCallback;
import com.google.android.gms.location.LocationRequest;
import com.google.android.gms.location.LocationResult;
import com.google.android.gms.location.LocationServices;
import com.google.android.gms.location.Priority;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicBoolean;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * Service au premier plan : suit la position et la poste lui-meme sur /field/position,
 * ce qui fonctionne meme si le JS de la WebView est gele.
 */
public class FieldLocationService extends Service {

    static final String EXTRA_PROFILE = "profile";
    static final String EXTRA_PUSH_MS = "pushMs";
    static final String EXTRA_POSTING = "posting";
    static final String EXTRA_SOS = "sos";

    private static final String TAG = "FieldLocation";
    private static final String KEY_TRACKING = "tracking";
    private static final int NOTIFICATION_ID = 4210;
    private static final long PUSH_HIGH_MS = 5000;
    private static final long PUSH_NORMAL_MS = 60000;
    private static final float MIN_MOVE_M = 5f;
    private static final long WAKE_LOCK_MS = 25000;

    private static volatile FieldLocationService instance;

    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private final AtomicBoolean postInFlight = new AtomicBoolean(false);
    private ExecutorService executor;
    private FusedLocationProviderClient locationClient;
    private PowerManager.WakeLock wakeLock;
    private boolean updatesActive;

    // Etats lus et ecrits sur le fil principal
    private String jsProfile = "normal";
    private String serverProfile;
    private String appliedProfile;
    private long jsPushMs = PUSH_NORMAL_MS;
    private Long serverPushMs;
    private boolean sos;
    private volatile boolean posting = true;
    private long lastPostAt;
    private Location lastPostedLocation;
    private JSONObject pendingBody;

    private final LocationCallback locationCallback = new LocationCallback() {
        @Override
        public void onLocationResult(@NonNull LocationResult result) {
            Location location = result.getLastLocation();
            if (location != null) onLocation(location);
        }
    };

    @Nullable
    static FieldLocationService running() {
        return instance;
    }

    static void markStopped(Context context) {
        FieldApi.prefs(context).edit().putBoolean(KEY_TRACKING, false).apply();
    }

    @Override
    public void onCreate() {
        super.onCreate();
        instance = this;
        executor = Executors.newSingleThreadExecutor();
        locationClient = LocationServices.getFusedLocationProviderClient(this);
        PowerManager power = getSystemService(PowerManager.class);
        wakeLock = power.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "field:position-post");
        wakeLock.setReferenceCounted(false);
        FieldNotifications.createChannels(this);
    }

    @Override
    public int onStartCommand(Intent intent, int flags, int startId) {
        // Toujours en premier : Android arrete le service sinon
        try {
            ServiceCompat.startForeground(this, NOTIFICATION_ID, buildNotification(), ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION);
        } catch (Exception e) {
            // Relance par le systeme en arriere-plan : Android 12 peut la refuser.
            // Le suivi reprendra a la prochaine ouverture de l'application.
            Log.w(TAG, "Demarrage au premier plan refuse : " + e.getMessage());
            stopSelf();
            return START_NOT_STICKY;
        }
        if (intent != null) {
            configure(
                intent.getStringExtra(EXTRA_PROFILE),
                intent.getLongExtra(EXTRA_PUSH_MS, PUSH_NORMAL_MS),
                intent.getBooleanExtra(EXTRA_POSTING, true),
                intent.getBooleanExtra(EXTRA_SOS, false)
            );
        } else {
            restoreAfterRestart();
        }
        return START_STICKY;
    }

    private void restoreAfterRestart() {
        android.content.SharedPreferences prefs = FieldApi.prefs(this);
        if (!prefs.getBoolean(KEY_TRACKING, false)) {
            stopSelf();
            return;
        }
        configure(prefs.getString(EXTRA_PROFILE, "normal"), prefs.getLong(EXTRA_PUSH_MS, PUSH_NORMAL_MS), true, false);
    }

    /** Appele sur le fil principal, par le plugin ou au demarrage. */
    void configure(@Nullable String profile, long pushMs, boolean postingEnabled, boolean sosActive) {
        jsProfile = profile == null ? "normal" : profile;
        jsPushMs = pushMs > 0 ? pushMs : PUSH_NORMAL_MS;
        posting = postingEnabled;
        sos = sosActive;
        FieldApi.prefs(this).edit()
            .putBoolean(KEY_TRACKING, true)
            .putString(EXTRA_PROFILE, jsProfile)
            .putLong(EXTRA_PUSH_MS, jsPushMs)
            .apply();
        applyProfile();
    }

    void setPosting(boolean enabled) {
        posting = enabled;
        if (!enabled) pendingBody = null;
    }

    void stopTracking(@Nullable String reason) {
        Log.i(TAG, "Arret du suivi : " + reason);
        markStopped(this);
        if (reason != null) {
            JSObject event = new JSObject();
            event.put("reason", reason);
            FieldEvents.emit("trackingStopped", event);
        }
        stopSelf();
    }

    private static int rank(@Nullable String profile) {
        if ("high".equals(profile)) return 2;
        if ("low".equals(profile)) return 0;
        return 1;
    }

    private void applyProfile() {
        // Le JS connait patrouille et SOS, le serveur le statut : le plus exigeant gagne
        String effective = rank(serverProfile) > rank(jsProfile) ? serverProfile : jsProfile;
        if (updatesActive && effective.equals(appliedProfile)) return;
        LocationRequest request;
        if ("high".equals(effective)) {
            request = new LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 2000).setMinUpdateIntervalMillis(1000).build();
        } else if ("low".equals(effective)) {
            request = new LocationRequest.Builder(Priority.PRIORITY_BALANCED_POWER_ACCURACY, 60000).setMinUpdateIntervalMillis(30000).build();
        } else {
            request = new LocationRequest.Builder(Priority.PRIORITY_HIGH_ACCURACY, 10000).setMinUpdateIntervalMillis(5000).build();
        }
        try {
            if (updatesActive) locationClient.removeLocationUpdates(locationCallback);
            locationClient.requestLocationUpdates(request, locationCallback, Looper.getMainLooper());
            updatesActive = true;
            appliedProfile = effective;
        } catch (SecurityException e) {
            stopTracking("permission_refusee");
        }
    }

    private long effectivePushMs() {
        return serverPushMs != null ? serverPushMs : jsPushMs;
    }

    private void onLocation(Location location) {
        JSObject event = new JSObject();
        event.put("lat", location.getLatitude());
        event.put("lng", location.getLongitude());
        event.put("accuracy", location.hasAccuracy() ? location.getAccuracy() : JSObject.NULL);
        event.put("speed", location.hasSpeed() ? location.getSpeed() : JSObject.NULL);
        event.put("heading", location.hasBearing() ? location.getBearing() : JSObject.NULL);
        if (lastPostAt > 0) event.put("lastPostAt", lastPostAt);
        FieldEvents.emit("location", event);

        if (!posting) return;
        long now = System.currentTimeMillis();
        boolean due = pendingBody != null
            || lastPostedLocation == null
            || now - lastPostAt >= effectivePushMs()
            || lastPostedLocation.distanceTo(location) >= MIN_MOVE_M;
        if (!due || postInFlight.get()) return;
        try {
            JSONObject body = buildBody(location, now);
            lastPostedLocation = location;
            lastPostAt = now;
            pendingBody = null;
            post(body);
        } catch (JSONException e) {
            Log.w(TAG, "Position illisible : " + e.getMessage());
        }
    }

    private JSONObject buildBody(Location location, long now) throws JSONException {
        JSONObject body = new JSONObject();
        body.put("lat", location.getLatitude());
        body.put("lng", location.getLongitude());
        body.put("accuracy", location.hasAccuracy() ? location.getAccuracy() : JSONObject.NULL);
        body.put("speed", location.hasSpeed() ? location.getSpeed() : JSONObject.NULL);
        body.put("heading", location.hasBearing() ? location.getBearing() : JSONObject.NULL);
        int battery = getSystemService(BatteryManager.class).getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY);
        body.put("battery", battery > 0 ? battery : JSONObject.NULL);
        body.put("ts", now);
        body.put("native", true);
        return body;
    }

    private void post(JSONObject body) {
        postInFlight.set(true);
        wakeLock.acquire(WAKE_LOCK_MS);
        executor.execute(() -> {
            FieldApi.Result result = null;
            boolean failed = false;
            try {
                result = FieldApi.postJson(this, "/field/position", body);
            } catch (Exception e) {
                failed = true;
                Log.d(TAG, "Envoi de position en echec : " + e.getMessage());
            }
            final FieldApi.Result finalResult = result;
            final boolean finalFailed = failed;
            mainHandler.post(() -> onPostDone(body, finalResult, finalFailed));
        });
    }

    private void onPostDone(JSONObject body, @Nullable FieldApi.Result result, boolean failed) {
        postInFlight.set(false);
        if (wakeLock.isHeld()) wakeLock.release();
        Log.d(TAG, "Envoi de position : " + (failed ? "echec reseau" : result == null ? "pas de session" : "HTTP " + result.code));
        if (failed || (result != null && result.code >= 500)) {
            // Hors ligne : seul le dernier point compte, renvoye au prochain fix
            pendingBody = body;
            return;
        }
        if (result == null) return;     // pas encore de session
        if (result.code == 401) {
            Log.i(TAG, "Refus du serveur : " + result.body);
            // not_paired, device_revoked, event_ended
            stopTracking("session_" + result.code);
            return;
        }
        if (result.code >= 200 && result.code < 300) applyServerState(result.body);
    }

    /** Adapte la cadence d'apres le serveur, meme si le JS est gele. */
    private void applyServerState(String responseBody) {
        try {
            JSONObject response = new JSONObject(responseBody);
            String status = response.optString("status", "");
            String mode = response.optString("tracking_mode", "");
            if (status.isEmpty() && mode.isEmpty()) return;     // ancien serveur
            if ("pause".equals(status) && !sos) {
                stopTracking("pause");
                return;
            }
            boolean highFrequency = "high_freq".equals(mode);
            serverPushMs = highFrequency ? PUSH_HIGH_MS : PUSH_NORMAL_MS;
            serverProfile = highFrequency || "intervention".equals(status) || "sur_place".equals(status) ? "high" : null;
            applyProfile();
        } catch (JSONException e) {
            Log.w(TAG, "Reponse de position illisible : " + e.getMessage());
        }
    }

    private Notification buildNotification() {
        return new NotificationCompat.Builder(this, FieldNotifications.CHANNEL_TRACKING)
            .setSmallIcon(R.drawable.ic_stat_field)
            .setContentTitle("Field : suivi actif")
            .setContentText("Votre position est transmise au PC Organisation")
            .setOngoing(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .setContentIntent(FieldNotifications.openAppIntent(this, null, NOTIFICATION_ID))
            .build();
    }

    @Override
    public void onDestroy() {
        instance = null;
        mainHandler.removeCallbacksAndMessages(null);
        if (locationClient != null) locationClient.removeLocationUpdates(locationCallback);
        if (executor != null) executor.shutdown();
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        super.onDestroy();
    }

    @Nullable
    @Override
    public IBinder onBind(Intent intent) {
        return null;
    }
}
