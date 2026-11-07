package org.lemans.cockpit.field;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.media.AudioAttributes;
import android.media.RingtoneManager;
import androidx.core.app.NotificationCompat;
import java.util.Map;

final class FieldNotifications {

    static final String CHANNEL_SOS = "field_sos";
    static final String CHANNEL_NORMAL = "field_normal";
    static final String CHANNEL_TRACKING = "field_tracking";
    static final String EXTRA_URL = "field_url";
    private static final long[] SOS_VIBRATION = { 0, 800, 300, 800, 300, 800, 300, 800, 300, 800 };

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

        NotificationChannel normal = new NotificationChannel(CHANNEL_NORMAL, "Messages et missions", NotificationManager.IMPORTANCE_HIGH);
        normal.setDescription("Messages, fiches et propositions du PC Organisation");
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
        String tag = data.get("tag");
        String url = data.get("url");
        int id = tag != null ? tag.hashCode() : (int) System.currentTimeMillis();
        PendingIntent open = openAppIntent(context, url, id);

        NotificationCompat.Builder builder = new NotificationCompat.Builder(context, isSos ? CHANNEL_SOS : CHANNEL_NORMAL)
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
                .setFullScreenIntent(open, true);
        }
        NotificationManager manager = context.getSystemService(NotificationManager.class);
        manager.notify(tag, id, builder.build());
    }
}
