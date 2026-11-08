package org.lemans.cockpit.field;

import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Rect;
import android.util.Base64;
import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.google.android.gms.tasks.Task;
import com.google.android.gms.tasks.Tasks;
import com.google.mlkit.vision.barcode.BarcodeScanner;
import com.google.mlkit.vision.barcode.BarcodeScanning;
import com.google.mlkit.vision.barcode.common.Barcode;
import com.google.mlkit.vision.common.InputImage;
import com.google.mlkit.vision.text.Text;
import com.google.mlkit.vision.text.TextRecognition;
import com.google.mlkit.vision.text.TextRecognizer;
import com.google.mlkit.vision.text.latin.TextRecognizerOptions;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;

/**
 * Lecture d'une image de la camera pour le controle de documents : texte (bande
 * MRZ, permis, badges) et codes. Modeles ML Kit embarques dans l'APK : tout se
 * fait sur la tablette, sans reseau, et l'image n'est jamais conservee.
 */
final class FieldScanner {

    interface Callback {
        void done(JSObject result);
        void failed(String error);
    }

    private static TextRecognizer textRecognizer;
    private static BarcodeScanner barcodeScanner;

    private FieldScanner() {}

    private static synchronized TextRecognizer text() {
        if (textRecognizer == null) textRecognizer = TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS);
        return textRecognizer;
    }

    private static synchronized BarcodeScanner barcodes() {
        if (barcodeScanner == null) barcodeScanner = BarcodeScanning.getClient();
        return barcodeScanner;
    }

    static void scan(String base64Jpeg, Callback callback) {
        Bitmap bitmap;
        try {
            byte[] bytes = Base64.decode(base64Jpeg, Base64.DEFAULT);
            bitmap = BitmapFactory.decodeByteArray(bytes, 0, bytes.length);
        } catch (IllegalArgumentException e) {
            bitmap = null;
        }
        if (bitmap == null) {
            callback.failed("image_illisible");
            return;
        }
        InputImage image = InputImage.fromBitmap(bitmap, 0);
        Task<Text> textTask = text().process(image);
        Task<List<Barcode>> codeTask = barcodes().process(image);
        final Bitmap toRecycle = bitmap;
        Tasks.whenAllComplete(textTask, codeTask).addOnCompleteListener(all -> {
            JSObject result = new JSObject();
            result.put("lines", textTask.isSuccessful() ? lines(textTask.getResult()) : new JSArray());
            result.put("barcodes", codeTask.isSuccessful() ? codes(codeTask.getResult()) : new JSArray());
            result.put("width", toRecycle.getWidth());
            result.put("height", toRecycle.getHeight());
            toRecycle.recycle();
            callback.done(result);
        });
    }

    /**
     * Lignes de haut en bas. Tri simple et transitif : une comparaison "meme rangee"
     * ne l'est pas, et TimSort peut alors lever une exception. field.js regroupe
     * ensuite les rangees et les ordonne de gauche a droite d'apres les cadres.
     */
    private static JSArray lines(Text text) {
        List<Text.Line> all = new ArrayList<>();
        for (Text.TextBlock block : text.getTextBlocks()) all.addAll(block.getLines());
        Collections.sort(all, (a, b) -> {
            Rect ra = a.getBoundingBox(), rb = b.getBoundingBox();
            int ya = ra == null ? 0 : ra.centerY(), yb = rb == null ? 0 : rb.centerY();
            if (ya != yb) return Integer.compare(ya, yb);
            return Integer.compare(ra == null ? 0 : ra.left, rb == null ? 0 : rb.left);
        });
        JSArray out = new JSArray();
        for (Text.Line line : all) {
            JSObject l = new JSObject();
            l.put("text", line.getText());
            Rect r = line.getBoundingBox();
            if (r != null) {
                JSArray box = new JSArray();
                box.put(r.left);
                box.put(r.top);
                box.put(r.right);
                box.put(r.bottom);
                l.put("box", box);
            }
            out.put(l);
        }
        return out;
    }

    private static JSArray codes(List<Barcode> list) {
        JSArray out = new JSArray();
        for (Barcode b : list) {
            String value = b.getRawValue();
            if (value == null || value.isEmpty()) continue;
            JSObject c = new JSObject();
            c.put("value", value);
            c.put("format", format(b.getFormat()));
            out.put(c);
        }
        return out;
    }

    /** Memes noms que BarcodeDetector, deja utilises par field.js. */
    private static String format(int f) {
        switch (f) {
            case Barcode.FORMAT_QR_CODE: return "qr_code";
            case Barcode.FORMAT_CODE_128: return "code_128";
            case Barcode.FORMAT_CODE_39: return "code_39";
            case Barcode.FORMAT_CODE_93: return "code_93";
            case Barcode.FORMAT_CODABAR: return "codabar";
            case Barcode.FORMAT_ITF: return "itf";
            case Barcode.FORMAT_EAN_13: return "ean_13";
            case Barcode.FORMAT_EAN_8: return "ean_8";
            case Barcode.FORMAT_UPC_A: return "upc_a";
            case Barcode.FORMAT_UPC_E: return "upc_e";
            case Barcode.FORMAT_DATA_MATRIX: return "data_matrix";
            case Barcode.FORMAT_AZTEC: return "aztec";
            case Barcode.FORMAT_PDF417: return "pdf417";
            default: return "autre";
        }
    }
}
