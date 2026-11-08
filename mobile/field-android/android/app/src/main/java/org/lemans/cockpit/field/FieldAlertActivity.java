package org.lemans.cockpit.field;

import android.app.Activity;
import android.app.KeyguardManager;
import android.content.Context;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.drawable.GradientDrawable;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.util.Log;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.WindowManager;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;
import androidx.annotation.Nullable;

/**
 * Alerte plein ecran, par-dessus le verrouillage, comme un appel entrant. N'affiche que le
 * texte de la notification : le reste de Field reste protege par le code de la tablette.
 */
public class FieldAlertActivity extends Activity {

    static final String EXTRA_KIND = "kind";
    static final String EXTRA_TITLE = "title";
    static final String EXTRA_BODY = "body";
    static final String EXTRA_TIMEOUT_MS = "timeoutMs";

    private static final String TAG = "FieldAlert";
    private static final long BLINK_MS = 500;
    private static final int RED = Color.rgb(185, 28, 28);
    private static final int DARK_RED = Color.rgb(69, 10, 10);

    private final Handler handler = new Handler(Looper.getMainLooper());
    private LinearLayout root;
    private TextView kindView;
    private TextView titleView;
    private TextView bodyView;
    private boolean blinkOn;
    @Nullable
    private String torchCameraId;

    private final Runnable blink = new Runnable() {
        @Override
        public void run() {
            blinkOn = !blinkOn;
            root.setBackgroundColor(blinkOn ? RED : DARK_RED);
            setTorch(blinkOn);
            handler.postDelayed(this, BLINK_MS);
        }
    };

    private final Runnable closeAfterTimeout = this::finish;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setShowWhenLocked(true);
        setTurnScreenOn(true);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        setContentView(buildView());
        torchCameraId = findTorchCamera();
        applyIntent(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        applyIntent(intent);
    }

    private void applyIntent(Intent intent) {
        String kind = intent.getStringExtra(EXTRA_KIND);
        kindView.setText("sos".equals(kind) ? "SOS" : "INTERVENTION");
        titleView.setText(intent.getStringExtra(EXTRA_TITLE));
        bodyView.setText(intent.getStringExtra(EXTRA_BODY));
        handler.removeCallbacks(closeAfterTimeout);
        long timeout = intent.getLongExtra(EXTRA_TIMEOUT_MS, 0);
        if (timeout > 0) handler.postDelayed(closeAfterTimeout, timeout);
    }

    @Override
    protected void onStart() {
        super.onStart();
        handler.post(blink);
    }

    @Override
    protected void onStop() {
        handler.removeCallbacks(blink);
        setTorch(false);
        super.onStop();
    }

    @Override
    protected void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        super.onDestroy();
    }

    @Override
    public void onBackPressed() {
        // Une alerte se traite par un des deux boutons
    }

    private void openField() {
        FieldNotifications.silenceAlarms(this);
        KeyguardManager keyguard = getSystemService(KeyguardManager.class);
        keyguard.requestDismissKeyguard(this, new KeyguardManager.KeyguardDismissCallback() {
            @Override
            public void onDismissSucceeded() {
                Intent open = new Intent(FieldAlertActivity.this, MainActivity.class).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP);
                String url = getIntent().getStringExtra(FieldNotifications.EXTRA_URL);
                if (url != null) open.putExtra(FieldNotifications.EXTRA_URL, url);
                startActivity(open);
                finish();
            }
        });
    }

    private void stopAlarm() {
        FieldNotifications.silenceAlarms(this);
        finish();
    }

    @Nullable
    private String findTorchCamera() {
        try {
            CameraManager cameras = getSystemService(CameraManager.class);
            for (String id : cameras.getCameraIdList()) {
                Boolean hasFlash = cameras.getCameraCharacteristics(id).get(CameraCharacteristics.FLASH_INFO_AVAILABLE);
                if (Boolean.TRUE.equals(hasFlash)) return id;
            }
        } catch (CameraAccessException | RuntimeException e) {
            Log.w(TAG, "Flash indisponible : " + e.getMessage());
        }
        return null;
    }

    private void setTorch(boolean on) {
        if (torchCameraId == null) return;
        try {
            getSystemService(CameraManager.class).setTorchMode(torchCameraId, on);
        } catch (CameraAccessException | RuntimeException e) {
            // Camera utilisee ailleurs : l'alerte continue sans le flash
            torchCameraId = null;
        }
    }

    private View buildView() {
        root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setPadding(dp(32), dp(48), dp(32), dp(48));
        root.setBackgroundColor(RED);

        kindView = text(44, true);
        titleView = text(30, true);
        bodyView = text(22, false);
        root.addView(kindView, spaced(dp(24)));
        root.addView(titleView, spaced(dp(16)));
        root.addView(bodyView, spaced(dp(48)));
        root.addView(button("VOIR LA MISSION", Color.WHITE, RED, v -> openField()), spaced(dp(16)));
        root.addView(button("ARRETER L'ALARME", DARK_RED, Color.WHITE, v -> stopAlarm()), spaced(0));
        return root;
    }

    private TextView text(int sizeSp, boolean bold) {
        TextView view = new TextView(this);
        view.setTextColor(Color.WHITE);
        view.setGravity(Gravity.CENTER);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, sizeSp);
        if (bold) view.setTypeface(view.getTypeface(), android.graphics.Typeface.BOLD);
        return view;
    }

    private Button button(String label, int background, int foreground, View.OnClickListener listener) {
        Button button = new Button(this);
        button.setText(label);
        button.setTextColor(foreground);
        button.setTextSize(TypedValue.COMPLEX_UNIT_SP, 20);
        button.setTypeface(button.getTypeface(), android.graphics.Typeface.BOLD);
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(background);
        shape.setCornerRadius(dp(14));
        button.setBackground(shape);
        button.setMinHeight(dp(72));
        button.setOnClickListener(listener);
        return button;
    }

    private LinearLayout.LayoutParams spaced(int bottom) {
        LinearLayout.LayoutParams params = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        params.bottomMargin = bottom;
        return params;
    }

    private int dp(int value) {
        return Math.round(TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value, getResources().getDisplayMetrics()));
    }

    static Intent intent(Context context, String kind, String title, String body, @Nullable String url, long timeoutMs) {
        Intent intent = new Intent(context, FieldAlertActivity.class)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP)
            .putExtra(EXTRA_KIND, kind)
            .putExtra(EXTRA_TITLE, title)
            .putExtra(EXTRA_BODY, body)
            .putExtra(EXTRA_TIMEOUT_MS, timeoutMs);
        if (url != null) intent.putExtra(FieldNotifications.EXTRA_URL, url);
        return intent;
    }
}
