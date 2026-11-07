package org.lemans.cockpit.field;

import android.content.Context;
import android.content.SharedPreferences;
import android.webkit.CookieManager;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.CookieHandler;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import org.json.JSONException;
import org.json.JSONObject;

/** Appels HTTP natifs vers Cockpit, avec le cookie de session de la WebView. */
final class FieldApi {

    static final String PREFS = "field_native";
    private static final String KEY_ORIGIN = "origin";
    private static final int TIMEOUT_MS = 10000;

    static final class Result {
        final int code;
        final String body;

        Result(int code, String body) {
            this.code = code;
            this.body = body;
        }
    }

    private FieldApi() {}

    static SharedPreferences prefs(Context context) {
        return context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    static void saveOrigin(Context context, String origin) {
        prefs(context).edit().putString(KEY_ORIGIN, origin).apply();
    }

    static String origin(Context context) {
        return prefs(context).getString(KEY_ORIGIN, null);
    }

    /** POST JSON. Retourne null si aucune session n'existe ou si l'origine est inconnue. */
    static Result postJson(Context context, String path, JSONObject body) throws IOException {
        String origin = origin(context);
        if (origin == null) return null;
        // field_token : HttpOnly, chemin /field
        String cookieUrl = origin + "/field";
        CookieManager cookies = CookieManager.getInstance();
        String cookie = cookies.getCookie(cookieUrl);
        if (cookie == null || cookie.isEmpty()) return null;

        HttpURLConnection connection = (HttpURLConnection) new URL(origin + path).openConnection();
        try {
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(TIMEOUT_MS);
            connection.setReadTimeout(TIMEOUT_MS);
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json");
            // Capacitor installe un CookieHandler qui ajoute deja le cookie. L'ajouter aussi
            // envoie deux en-tetes, que le serveur fusionne en un jeton inconnu (401).
            // Sans lui (service relance seul par Android), il faut le poser ici.
            if (CookieHandler.getDefault() == null) {
                connection.setRequestProperty("Cookie", cookie);
            }
            byte[] payload = body.toString().getBytes(StandardCharsets.UTF_8);
            connection.setFixedLengthStreamingMode(payload.length);
            try (OutputStream out = connection.getOutputStream()) {
                out.write(payload);
            }
            int code = connection.getResponseCode();
            storeCookies(cookies, cookieUrl, connection.getHeaderFields());
            return new Result(code, readBody(code < 400 ? connection.getInputStream() : connection.getErrorStream()));
        } finally {
            connection.disconnect();
        }
    }

    /** Session glissante : sans cela, une tablette restee verrouillee finit par la perdre. */
    private static void storeCookies(CookieManager cookies, String cookieUrl, Map<String, List<String>> headers) {
        boolean stored = false;
        for (Map.Entry<String, List<String>> header : headers.entrySet()) {
            if (header.getKey() == null || !"set-cookie".equalsIgnoreCase(header.getKey())) continue;
            for (String value : header.getValue()) {
                cookies.setCookie(cookieUrl, value);
                stored = true;
            }
        }
        if (stored) cookies.flush();
    }

    private static String readBody(InputStream stream) throws IOException {
        if (stream == null) return "";
        try (InputStream in = stream) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[4096];
            int read;
            while ((read = in.read(buffer)) != -1) out.write(buffer, 0, read);
            return out.toString("UTF-8");
        }
    }

    static void registerFcmToken(Context context, String token) throws IOException, JSONException {
        JSONObject body = new JSONObject();
        body.put("token", token);
        body.put("app_version", BuildConfig.VERSION_NAME);
        postJson(context, "/field/push/fcm-register", body);
    }
}
