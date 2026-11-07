package org.lemans.cockpit.field;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.media.AudioAttributes;
import android.media.AudioManager;
import android.media.RingtoneManager;
import android.service.notification.StatusBarNotification;
import android.util.Log;
import androidx.core.app.NotificationCompat;
import java.util.Map;

final class FieldNotifications {

    static final String CHANNEL_SOS = "field_sos";
    static final String CHANNEL_MISSION = "field_mission";
    static final String CHANNEL_NORMAL = "field_normal";
    static final String CHANNEL_TRACKING = "field_tracking";
    static final String EXTRA_URL = "field_url";
    private static final String TAG = "FieldNotifications";
    private static final long[] SOS_VIBRATION = { 0, 800, 300, 800, 300, 800, 300, 800, 300, 800 };
    private static final long[] MISSION_VIBRATION = { 0, 1000, 500, 1000, 500, 1000 };
    // Une alarme ignoree s'arrete seule : tablette oubliee dans un vehicule, proposition expiree
    private static final long MISSION_ALARM_MS = 120000;

    private FieldNotifications() {}

    static void createChannels(Context context) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);

        NotificationChannel sos = new NotificationChannel(CHANNEL_SOS, "SOS", NotificationManager.IMPORTANCE_HIGH);
        sos.setDescription("Alertes SOS des autres tablettes");
        sos.enableVibration(true);
        sos.setVibrationPattern(SOS_VIBRATION);
        sos.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
        sos.setSound(
            RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM),
            new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
        );
        manager.createNotificationChannel(sos);

        NotificationChannel mission = new NotificationChannel(CHANNEL_MISSION, "Fiches et patrouilles", NotificationManager.IMPORTANCE_HIGH);
        mission.setDescription("Fiche affectee ou proposee, patrouille proposee : alarme jusqu'a la prise en compte");
        mission.enableVibration(true);
        mission.setVibrationPattern(MISSION_VIBRATION);
        mission.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
        mission.setSound(
            RingtoneManager.getDefaultUri(RingtoneManager.TYPE_ALARM),
            new AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_ALARM).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()
        );
        manager.createNotificationChannel(mission);

        NotificationChannel normal = new NotificationChannel(CHANNEL_NORMAL, "Messages et missions", NotificationManager.IMPORTANCE_HIGH);
        normal.setDescription("Messages du PC Organisation");
        normal.enableVibration(true);
        manager.createNotificationChannel(normal);

        NotificationChannel tracking = new NotificationChannel(CHANNEL_TRACKING, "Suivi de position", NotificationManager.IMPORTANCE_LOW);
        tracking.setDescription("Indique que la position est transmise au PC Organisation");
        tracking.setShowBadge(false);
        manager.createNotificationChannel(tracking);
    }

    static PendingIntent openAppIntent(Context context, String url, int requestCode) {
        Intent intent = new Intent(context, MainActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
        if (url != null) intent.putExtra(EXTRA_URL, url);
        return PendingIntent.getActivity(context, requestCode, intent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    /** Affiche une notification depuis un message data FCM : title, body, url, tag, type. */
    static void show(Context context, Map<String, String> data) {
        createChannels(context);
        boolean isSos = "sos".equals(data.get("type"));
        // Application a l'ecran : field.js alerte deja, pas besoin d'alarme
        boolean isMission = "mission".equals(data.get("type")) && !MainActivity.isInForeground();
        String tag = data.get("tag");
        String url = data.get("url");
        int id = tag != null ? tag.hashCode() : (int) System.currentTimeMillis();
        PendingIntent open = openAppIntent(context, url, id);

        NotificationCompat.Builder builder = new NotificationCompat.Builder(context, isSos ? CHANNEL_SOS : isMission ? CHANNEL_MISSION : CHANNEL_NORMAL)
            .setSmallIcon(R.drawable.ic_stat_field)
            .setContentTitle(data.get("title"))
            .setContentText(data.get("body"))
            .setStyle(new NotificationCompat.BigTextStyle().bigText(data.get("body")))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setPriority(NotificationCompat.PRIORITY_MAX);
        if (isSos) {
            builder
                .setCategory(NotificationCompat.CATEGORY_ALARM)
                .setOngoing(true)
                .setVibrate(SOS_VIBRATION)
                .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
                .setFullScreenIntent(alertIntent(context, "sos", data, url, 0, id), true);
        }
        if (isMission) {
            builder
                .setCategory(NotificationCompat.CATEGORY_ALARM)
                .setVibrate(MISSION_VIBRATION)
                .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
                .setTimeoutAfter(MISSION_ALARM_MS)
                .setFullScreenIntent(alertIntent(context, "mission", data, url, MISSION_ALARM_MS, id), true);
        }
        Notification notification = builder.build();
        if (isSos || isMission) {
            // Le son tourne en boucle jusqu'a la prise en compte, au volume maximal
            notification.flags |= Notification.FLAG_INSISTENT;
            maximizeAlarmVolume(context);
        }
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        manager.notify(tag, id, notification);
    }

    private static PendingIntent alertIntent(Context context, String kind, Map<String, String> data, String url, long timeoutMs, int id) {
        Intent intent = FieldAlertActivity.intent(context, kind, data.get("title"), data.get("body"), url, timeoutMs);
        return PendingIntent.getActivity(context, id + 1, intent, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }

    private static void maximizeAlarmVolume(Context context) {
        try {
            AudioManager audio = context.getSystemService(AudioManager.class);
            audio.setStreamVolume(AudioManager.STREAM_ALARM, audio.getStreamMaxVolume(AudioManager.STREAM_ALARM), 0);
        } catch (RuntimeException e) {
            Log.w(TAG, "Volume d'alarme inchange : " + e.getMessage());
        }
    }

    /** L'agent a ouvert l'application, quel que soit le chemin : l'alarme s'arrete. */
    static void silenceAlarms(Context context) {
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        for (StatusBarNotification active : manager.getActiveNotifications()) {
            String channel = active.getNotification().getChannelId();
            if (CHANNEL_SOS.equals(channel) || CHANNEL_MISSION.equals(channel)) {
                manager.cancel(active.getTag(), active.getId());
            }
        }
    }
}
