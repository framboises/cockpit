package org.lemans.cockpit.field;

import com.getcapacitor.JSObject;

/** Relais des services natifs vers la page, tant que l'activite existe. */
final class FieldEvents {

    interface Listener {
        void onEvent(String name, JSObject data, boolean retainUntilConsumed);
    }

    private static volatile Listener listener;

    private FieldEvents() {}

    static void setListener(Listener value) {
        listener = value;
    }

    static void clearListener(Listener value) {
        if (listener == value) listener = null;
    }

    static void emit(String name, JSObject data) {
        Listener current = listener;
        if (current != null) current.onEvent(name, data, false);
    }
}
