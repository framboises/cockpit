using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.Math;
using Toybox.Time;

// Meteo : etat du mur (temperature, vent, rafales), pluie a venir, et la
// consigne redigee cote serveur. Lu depuis Pages.bloc(Cache.loadPages(),
// "me") -- cf. watch_pages.build_meteo. Le WBGT n'apparait pas ici : il est
// deja en page 1, le repeter serait dire deux fois la meme chose.
//
// Le mur decide, la montre repete : AUCUN SEUIL METEO n'est ecrit dans ce
// fichier. La consigne (`cn`) est du texte redige par le serveur, affiche
// tel quel et colore par son niveau (`cl`) -- jamais reinterprete depuis les
// valeurs brutes (temperature/vent/rafale/pluie).
//
// Contrairement a la main courante et a la frequentation, Waze^H^H la meteo
// tourne toute l'annee (watch_state.py, meme constructeur en mode live et
// past) : cette vue n'a pas de cas "hors evenement".
class MeteoView extends WatchUi.View {

    function initialize() {
        View.initialize();
    }

    // Meme geometrie que les autres vues : sur un cadran rond, la place
    // utile depend de l'eloignement au centre.
    hidden function largeurUtile(dc, y) {
        var r = dc.getWidth() / 2.0;
        var dy = (y - r).abs();
        if (dy >= r) {
            return 0.0;
        }
        return 2.0 * Math.sqrt(r * r - dy * dy);
    }

    // Meme echelle 0-3 que Pages.verdictMot/TraficView.couleurVerdict --
    // `cl` (consigne) et `vg` (vigilance Meteo-France) partagent cette
    // echelle. La couleur seule ne porte jamais une identite : chaque appel
    // est toujours accompagne d'un texte (la consigne elle-meme, ou le mot
    // "VIGILANCE" pres du titre).
    hidden function couleurNiveau(n) {
        if (n == null) { return Graphics.COLOR_DK_GRAY; }
        if (n >= 3) { return Graphics.COLOR_RED; }
        if (n == 2) { return Graphics.COLOR_ORANGE; }
        if (n == 1) { return Graphics.COLOR_YELLOW; }
        return Graphics.COLOR_GREEN;
    }

    hidden function formatTemp(tc) {
        if (tc == null) { return Fmt.DASH; }
        return tc.format("%.1f") + " C";
    }

    hidden function formatKmh(n) {
        if (n == null) { return Fmt.DASH; }
        return n.format("%.0f") + " km/h";
    }

    hidden function formatMmh(n) {
        if (n == null) { return Fmt.DASH; }
        return n.format("%.1f") + " mm/h";
    }

    // Troncature caractere par caractere jusqu'a rentrer dans `dispo` --
    // meme filet de securite que TraficView.ajusterNom, sur un texte dont la
    // provenance differe (redige par le serveur, borne a 44 caracteres cote
    // watch_pages.CONSIGNE_MAX) mais qui n'est pas davantage sous controle
    // de cette vue.
    hidden function ajusterTexte(dc, texte, font, dispo) {
        if (dc.getTextWidthInPixels(texte, font) <= dispo) {
            return texte;
        }
        var t = texte;
        while (t.length() > 1 &&
               dc.getTextWidthInPixels(t, font) > dispo) {
            t = t.substring(0, t.length() - 1);
        }
        return t;
    }

    // Coupe la consigne sur au plus deux lignes, a la derniere espace qui
    // tient dans `dispo1` (la corde de la premiere ligne). La consigne
    // (<=44 caracteres) est le texte le plus large de la page -- elle ne
    // tient pas toujours en une ligne en FONT_SMALL (mesure a la sonde).
    // Rend un tableau de 1 ou 2 chaines, jamais plus : la seconde ligne est
    // elle-meme tronquee par ajusterTexte si le reliquat ne tenait pas non
    // plus dans sa propre corde (dispo2).
    hidden function couperConsigne(dc, texte, font, dispo1, dispo2) {
        if (dc.getTextWidthInPixels(texte, font) <= dispo1) {
            return [texte];
        }
        var coupure = -1;
        for (var i = 0; i < texte.length(); i += 1) {
            if (texte.substring(i, i + 1).equals(" ")) {
                var candidat = texte.substring(0, i);
                if (dc.getTextWidthInPixels(candidat, font) <= dispo1) {
                    coupure = i;
                } else {
                    break;
                }
            }
        }
        if (coupure < 0) {
            // Aucun mot entier ne tient (texte tres large ou corde tres
            // etroite) : on tronque brutalement la premiere ligne plutot
            // que de deborder, et il n'y a pas de seconde ligne exploitable.
            return [ajusterTexte(dc, texte, font, dispo1)];
        }
        var ligne1 = texte.substring(0, coupure);
        var reste = texte.substring(coupure + 1, texte.length());
        return [ligne1, ajusterTexte(dc, reste, font, dispo2)];
    }

    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();
        var w = dc.getWidth();
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var hS = dc.getFontHeight(Graphics.FONT_SMALL);
        var hM = dc.getFontHeight(Graphics.FONT_MEDIUM);

        var meteo = Pages.bloc(Cache.loadPages(), "meteo");

        // Liseré de vigilance Meteo-France, sous le texte (dessine en
        // premier) : un anneau proche du bord, jamais affirmatif sur un etat
        // qu'on ignore -- seulement quand le bloc est present ET vg >= 1.
        // Rayon et epaisseur choisis pour rester tres a l'interieur de la
        // marge deja mesuree sur les autres vues (>70 px) au pied de page,
        // le seul texte qui s'approche du bord.
        if (meteo != null) {
            var vg = meteo["vg"];
            if (vg != null && vg >= 1) {
                dc.setColor(couleurNiveau(vg), Graphics.COLOR_TRANSPARENT);
                dc.setPenWidth(5);
                dc.drawCircle(w / 2, dc.getHeight() / 2, w / 2 - 10);
                dc.setPenWidth(1);
            }
        }

        // Titre : moitie haute (y=26 < 227), meme position fixe que les
        // autres vues -- toujours rendu, bloc present ou non.
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 26, Graphics.FONT_XTINY, "METEO",
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Strate 1 : l'instant. Temperature en gros (FONT_MEDIUM, meme poids
        // que le WBGT en page 1), vent/rafale en dessous.
        var y = 26 + hX + 14;
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_MEDIUM,
                    formatTemp(meteo != null ? meteo["tc"] : null),
                    Graphics.TEXT_JUSTIFY_CENTER);

        y += hM + 4;
        var v = (meteo != null) ? meteo["v"] : null;
        var rf = (meteo != null) ? meteo["rf"] : null;
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_XTINY,
                    "vent " + formatKmh(v) + " . rafales " + formatKmh(rf),
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Strate 2 : la pluie a venir. Trois etats distincts -- bloc absent
        // (on ne sait pas), bloc present sans pluie attendue (`pl == null`,
        // un fait connu, jamais ecrit "0"), bloc present avec pluie
        // attendue (`pl` en minutes + `pm` au pic).
        y += hX + 14;
        if (meteo == null) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, "pluie --",
                        Graphics.TEXT_JUSTIFY_CENTER);
            y += hX + 14;
        } else if (meteo["pl"] == null) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, "pas de pluie attendue",
                        Graphics.TEXT_JUSTIFY_CENTER);
            y += hX + 14;
        } else {
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_SMALL,
                        "pluie dans " + meteo["pl"].toString() + " min",
                        Graphics.TEXT_JUSTIFY_CENTER);
            var yPic = y + hS - 2;
            dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, yPic, Graphics.FONT_XTINY,
                        formatMmh(meteo["pm"]) + " au pic",
                        Graphics.TEXT_JUSTIFY_CENTER);
            y = yPic + hX + 14;
        }

        // Strate 3 : la consigne -- le texte le plus important de la page,
        // redige par le mur et affiche tel quel, colore par `cl`. Trois
        // etats : bloc absent (tirets), bloc present sans consigne active
        // (fait connu, "aucune consigne"), bloc present avec consigne
        // (coloree, sur une ou deux lignes selon la largeur disponible).
        if (meteo == null) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, Fmt.DASH,
                        Graphics.TEXT_JUSTIFY_CENTER);
        } else if (meteo["cn"] == null) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, "aucune consigne",
                        Graphics.TEXT_JUSTIFY_CENTER);
        } else {
            var dispo1 = largeurUtile(dc, y);
            var dispo2 = largeurUtile(dc, y + hS - 2);
            var lignes = couperConsigne(dc, meteo["cn"], Graphics.FONT_SMALL,
                                        dispo1, dispo2);
            dc.setColor(couleurNiveau(meteo["cl"]), Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_SMALL, lignes[0],
                        Graphics.TEXT_JUSTIFY_CENTER);
            if (lignes.size() > 1) {
                dc.drawText(w / 2, y + hS - 2, Graphics.FONT_SMALL, lignes[1],
                            Graphics.TEXT_JUSTIFY_CENTER);
            }
        }

        // Pied de page : age de la donnee, ou son absence nommee
        // explicitement. Moitie basse, meme position fixe que les autres
        // vues.
        var yFoot = dc.getHeight() - hX - 17;
        if (meteo == null) {
            dc.setColor(Graphics.COLOR_RED, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, yFoot, Graphics.FONT_XTINY, "indisponible",
                        Graphics.TEXT_JUSTIFY_CENTER);
        } else {
            var now = Time.now().value();
            var age = (meteo["t"] != null) ? (now - meteo["t"]) : null;
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, yFoot, Graphics.FONT_XTINY,
                        "maj " + Fmt.age(age), Graphics.TEXT_JUSTIFY_CENTER);
        }
    }
}


class MeteoDelegate extends WatchUi.BehaviorDelegate {

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
