using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.Math;
using Toybox.Time;

// Frequentation : pic de presents du jour et son heure, compare au pic N-1
// au jour EQUIVALENT (meme decalage a la course, watch_pages.build_
// frequentation), et le pic de l'edition rapportee -- ce dernier vit dans
// le NOYAU (Cache.load(), champs "pk"/"pkt"), pas dans le bloc "st" des
// pages : attention a la collision de noms, le bloc de ce fichier s'appelle
// aussi "st" (cle Pages.bloc(pg, "st")) sans rapport avec la cle de
// Storage du noyau (Cache.KEY == "st"). Les deux variables locales
// ci-dessous portent donc des noms distincts (`noyau` / `freq`) pour ne
// jamais les confondre a la lecture.
//
// Contrairement a la meteo et au trafic, la frequentation n'a pas d'objet
// hors evenement : watch_state.py ne construit meme pas le bloc en mode
// past (comparer aujourd'hui a N-1 n'a pas de sens sans "aujourd'hui").
class FrequentationView extends WatchUi.View {

    function initialize() {
        View.initialize();
    }

    // Delta N-1 en pourcentage entier, arrondi. `null` si l'un des deux
    // termes manque ou si `n1` vaut zero (division impossible) -- jamais un
    // delta invente. Publique (pas `hidden`) pour rester testable en valeur
    // depuis DessinTest.mc, seul calcul non trivial de cette vue.
    function calculDeltaPct(pj, n1) {
        if (pj == null || n1 == null || n1 == 0) {
            return null;
        }
        return Math.round((pj - n1).toFloat() * 100.0 / n1.toFloat()).toNumber();
    }

    hidden function formatDelta(delta) {
        if (delta == null) {
            return Fmt.DASH;
        }
        if (delta > 0) {
            return "+" + delta.toString() + "%";
        }
        return delta.toString() + "%";
    }

    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();

        var w = dc.getWidth();
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var hS = dc.getFontHeight(Graphics.FONT_SMALL);
        var hN = dc.getFontHeight(Graphics.FONT_NUMBER_MEDIUM);

        // Titre : moitie haute, position fixe partagee avec les autres
        // vues -- toujours rendu, quel que soit l'etat qui suit.
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 26, Graphics.FONT_XTINY, "FREQUENTATION",
                    Graphics.TEXT_JUSTIFY_CENTER);

        var noyau = Cache.load();

        // Mode "past" (hors evenement) prime sur tout, meme regle que
        // MainCouranteView (tache 10) : comparer un pic du jour a un N-1
        // n'a pas de sens hors saison, et le bloc "st" n'existe meme pas
        // cote serveur dans ce mode -- ce n'est pas un "bloc absent"
        // (on ne sait pas) mais un etat "sans objet" (la question ne se
        // pose pas), qui doit se lire differemment.
        if (State.isPast(noyau)) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, dc.getHeight() / 2 - hS / 2, Graphics.FONT_SMALL,
                        "hors evenement", Graphics.TEXT_JUSTIFY_CENTER);
            return;
        }

        var freq = Pages.bloc(Cache.loadPages(), "st");

        // Strate 1 : pic du jour, en gros -- le chiffre le plus utile de
        // la page, meme poids visuel que le heros de la page 1. `pj`/`ph`
        // peuvent valoir null avec `freq` present (aucun pic releve pour
        // l'instant aujourd'hui, un etat reel tot dans la journee) --
        // Fmt.count/le repli sur DASH le rendent nativement.
        var y = 26 + hX + 10;
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_XTINY, "pic du jour",
                    Graphics.TEXT_JUSTIFY_CENTER);

        y += hX + 4;
        var pj = (freq != null) ? freq["pj"] : null;
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_NUMBER_MEDIUM, Fmt.count(pj),
                    Graphics.TEXT_JUSTIFY_CENTER);

        y += hN + 2;
        var ph = (freq != null) ? freq["ph"] : null;
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_XTINY,
                    (ph != null) ? ("a " + ph) : Fmt.DASH,
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Strate 2 : N-1 au jour equivalent, et son delta -- calcule ICI,
        // sur la montre, a partir de `pj`/`n1` : le serveur ne l'envoie pas.
        y += hX + 16;
        var n1 = (freq != null) ? freq["n1"] : null;
        var delta = calculDeltaPct(pj, n1);
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_SMALL,
                    "N-1 " + Fmt.count(n1) + " (" + formatDelta(delta) + ")",
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Strate 3 : pic de l'edition rapportee -- lu dans le NOYAU
        // (pk/pkt), independant du bloc "st" : reste affiche meme si "st"
        // est absent, comme la page 1 le fait deja en mode past.
        y += hS + 14;
        var pk = (noyau != null) ? noyau["pk"] : null;
        var pkt = (noyau != null) ? noyau["pkt"] : null;
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_XTINY,
                    "edition " + Fmt.count(pk) + " . " + Fmt.day(pkt) + " "
                    + Fmt.hour(pkt),
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Pied de page : age du bloc "st", ou son absence nommee
        // explicitement -- distinct du mode past deja court-circuite plus
        // haut. Moitie basse, meme position fixe que les autres vues.
        var yFoot = dc.getHeight() - hX - 17;
        if (freq == null) {
            dc.setColor(Graphics.COLOR_RED, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, yFoot, Graphics.FONT_XTINY, "indisponible",
                        Graphics.TEXT_JUSTIFY_CENTER);
        } else {
            var now = Time.now().value();
            var age = (freq["t"] != null) ? (now - freq["t"]) : null;
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, yFoot, Graphics.FONT_XTINY,
                        "maj " + Fmt.age(age), Graphics.TEXT_JUSTIFY_CENTER);
        }
    }
}


class FrequentationDelegate extends WatchUi.BehaviorDelegate {

    hidden var mView;

    function initialize(view) {
        BehaviorDelegate.initialize();
        mView = view;
    }

    function onBack() {
        WatchUi.popView(WatchUi.SLIDE_DOWN);
        return true;
    }
}
