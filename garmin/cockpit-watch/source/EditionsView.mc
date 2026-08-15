using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.Math;

// Consultation des editions passees : libelle, pic de presents, jour et heure
// du pic. Une seule requete rapporte la liste ET tous les pics, donc rien
// n'est demande edition par edition en faisant defiler.
class EditionsView extends WatchUi.View {

    // Quatre entrees par ecran, mesure sur le device (sonde de mise en page,
    // buffer hors-ecran) : la quatrieme finit a 318 px sur 454, soit 100 px
    // avant le repere de defilement. Cinq tiendraient encore -- la cinquieme
    // finit a 382 -- mais elles se serrent la ou la corde du cadran rond se
    // retrecit le plus vite, pour n'economiser qu'un cran de defilement.
    hidden const PAR_ECRAN = 4;

    hidden var mEditions = null;
    hidden var mErreur = false;
    hidden var mHaut = 0;

    function initialize() {
        View.initialize();
    }

    function onShow() {
        // La liste survit a la fermeture de la vue : le pic d'une edition
        // close ne change plus, la redemander a chaque ouverture du menu
        // serait du trafic BLE pur perte.
        if (mEditions == null) {
            Api.fetchEditions(method(:onFetched));
        }
    }

    function onFetched(ok, editions) as Void {
        if (ok && editions != null) {
            mEditions = editions;
            mErreur = false;
        } else if (mEditions == null) {
            mErreur = true;
        }
        WatchUi.requestUpdate();
    }

    function scroll(pas) {
        if (mEditions == null || mEditions.size() == 0) {
            return;
        }
        var max = mEditions.size() - PAR_ECRAN;
        if (max < 0) { max = 0; }
        mHaut += pas;
        if (mHaut < 0) { mHaut = 0; }
        if (mHaut > max) { mHaut = max; }
        WatchUi.requestUpdate();
    }

    // Meme geometrie que CockpitView : sur un cadran rond, la place utile
    // depend de l'eloignement au centre.
    hidden function largeurUtile(dc, y) {
        var r = dc.getWidth() / 2.0;
        var dy = (y - r).abs();
        if (dy >= r) {
            return 0.0;
        }
        return 2.0 * Math.sqrt(r * r - dy * dy);
    }

    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();

        var w = dc.getWidth();
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var hS = dc.getFontHeight(Graphics.FONT_SMALL);

        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 26, Graphics.FONT_XTINY, "PICS PAR EDITION",
                    Graphics.TEXT_JUSTIFY_CENTER);

        if (mEditions == null) {
            dc.setColor(mErreur ? Graphics.COLOR_RED : Graphics.COLOR_DK_GRAY,
                        Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, dc.getHeight() / 2 - hS / 2, Graphics.FONT_SMALL,
                        mErreur ? "indisponible" : "chargement...",
                        Graphics.TEXT_JUSTIFY_CENTER);
            return;
        }

        if (mEditions.size() == 0) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, dc.getHeight() / 2 - hS / 2, Graphics.FONT_SMALL,
                        "aucune edition", Graphics.TEXT_JUSTIFY_CENTER);
            return;
        }

        var y = 26 + hX + 14;
        var fin = mHaut + PAR_ECRAN;
        if (fin > mEditions.size()) { fin = mEditions.size(); }

        for (var i = mHaut; i < fin; i += 1) {
            var e = mEditions[i];
            var marge = (w - largeurUtile(dc, y)) / 2.0 + 6;

            // Libelle a gauche, pic a droite : les chiffres s'alignent d'une
            // ligne a l'autre, donc se comparent d'un regard.
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
            dc.drawText(marge, y, Graphics.FONT_SMALL, e[0],
                        Graphics.TEXT_JUSTIFY_LEFT);
            dc.drawText(w - marge, y, Graphics.FONT_SMALL, Fmt.count(e[1]),
                        Graphics.TEXT_JUSTIFY_RIGHT);

            var yQuand = y + hS - 2;
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w - ((w - largeurUtile(dc, yQuand)) / 2.0 + 6), yQuand,
                        Graphics.FONT_XTINY,
                        Fmt.day(e[2]) + " " + Fmt.hour(e[2]),
                        Graphics.TEXT_JUSTIFY_RIGHT);

            y = yQuand + hX + 10;
        }

        // Reperes de defilement : sans eux, rien ne dit qu'il reste des
        // editions au-dessus ou au-dessous.
        dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
        if (mHaut > 0) {
            dc.drawText(w / 2, 8, Graphics.FONT_XTINY, "^",
                        Graphics.TEXT_JUSTIFY_CENTER);
        }
        if (fin < mEditions.size()) {
            dc.drawText(w / 2, dc.getHeight() - hX - 12, Graphics.FONT_XTINY,
                        "v", Graphics.TEXT_JUSTIFY_CENTER);
        }
    }
}


class EditionsDelegate extends WatchUi.BehaviorDelegate {

    hidden var mView;

    function initialize(view) {
        BehaviorDelegate.initialize();
        mView = view;
    }

    function onNextPage() {
        mView.scroll(1);
        return true;
    }

    function onPreviousPage() {
        mView.scroll(-1);
        return true;
    }

    function onBack() {
        WatchUi.popView(WatchUi.SLIDE_DOWN);
        return true;
    }
}
