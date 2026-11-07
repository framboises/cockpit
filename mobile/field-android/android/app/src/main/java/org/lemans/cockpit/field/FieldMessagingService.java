package org.lemans.cockpit.field;

import android.util.Log;
import androidx.annotation.NonNull;
import com.getcapacitor.JSObject;
import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;
import java.util.Map;

public class FieldMessagingService extends FirebaseMessagingService {

    private static final String TAG = "FieldMessaging";

    @Override
    public void onMessageReceived(@NonNull RemoteMessage message) {
        Map<String, String> data = message.getData();
        if (data.isEmpty()) return;
        FieldNotifications.show(this, data);
        JSObject event = new JSObject();
        event.put("type", data.get("type"));
        event.put("tag", data.get("tag"));
        event.put("url", data.get("url"));
        FieldEvents.emit("pushReceived", event);
    }

    @Override
    public void onNewToken(@NonNull String token) {
        // Appele sur un fil de fond : l'appel reseau est permis ici
        try {
            FieldApi.registerFcmToken(this, token);
        } catch (Exception e) {
            // Nouvel essai a la prochaine ouverture de l'application
            Log.w(TAG, "Jeton FCM non remonte : " + e.getMessage());
        }
    }
}
