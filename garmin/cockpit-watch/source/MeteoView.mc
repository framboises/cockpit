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
// Contrairement a la main courante et a la frequentation, la meteo tourne
// toute l'annee (watch_state.py, meme constructeur en mode live et past) :
// cette vue n'a pas de cas "hors evenement".
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
    // provenance differe (redige par le serveur, borne a CONSIGNE_MAX
    // caracteres cote watch_pages.py) mais qui n'est pas davantage sous
    // controle de cette vue.
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
    // (<=CONSIGNE_MAX caracteres, watch_pages.py) est le texte le plus
    // large de la page -- elle ne tient pas toujours en une ligne, meme en
    // FONT_XTINY (mesure au device, appelant reel : onUpdate ci-dessous).
    // Rend un tableau de 1 ou 2 chaines,
    // jamais plus : la seconde ligne est elle-meme tronquee par
    // ajusterTexte si le reliquat ne tenait pas non plus dans sa propre
    // corde (dispo2). Publique (meme raison que FrequentationView.
    // calculDeltaPct) pour rester testable en VALEUR sur device : une
    // troncature au milieu d'un mot ne leve jamais d'exception.
    function couperConsigne(dc, texte, font, dispo1, dispo2) {
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
        var hM = dc.getFontHeight(Graphics.FONT_MEDIUM);

        var meteo = Pages.bloc(Cache.loadPages(), "me");

        // Titre : mesure au device (fenix8solar51mm, 280x280, rayon 140 --
        // PAS 227, l'erreur qui a motive la reprise complete de cette page).
        // Toujours rendu, bloc present ou non.
        var y = 24;
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_XTINY, "METEO",
                    Graphics.TEXT_JUSTIFY_CENTER);
        y += hX + 2;

        // Vigilance Meteo-France : le MOT d'abord, la couleur en renfort --
        // jamais la couleur seule (l'ancien lisere, retire : sur un ecran de
        // 280, un anneau assez large pour se voir couvre le pied de page
        // dans TOUS les cas, cf. sonde/rapport de tache -- << c'est la place
        // qui tranche >>, et la place n'a pas suffi). Absente quand vg est
        // inconnu OU vert (Pages.vigilanceMot rend null dans les deux cas),
        // meme regle que la bande de voyants de la page 1 : rien a signaler
        // ne s'affiche pas. DISTINCTE de la consigne (cl/cn) plus bas --
        // deux echelles independantes, cf. CLAUDE.md.
        var vg = (meteo != null) ? meteo["vg"] : null;
        var motVigilance = Pages.vigilanceMot(vg);
        if (motVigilance != null) {
            dc.setColor(couleurNiveau(vg), Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, motVigilance,
                        Graphics.TEXT_JUSTIFY_CENTER);
            y += hX + 2;
        }

        // Strate 1 : l'instant. Temperature en gros (FONT_MEDIUM, meme poids
        // que le WBGT en page 1), vent/rafale en dessous.
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_MEDIUM,
                    formatTemp(meteo != null ? meteo["tc"] : null),
                    Graphics.TEXT_JUSTIFY_CENTER);
        y += hM + 2;

        var v = (meteo != null) ? meteo["v"] : null;
        var rf = (meteo != null) ? meteo["rf"] : null;
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_XTINY,
                    "vent " + formatKmh(v) + " . rafales " + formatKmh(rf),
                    Graphics.TEXT_JUSTIFY_CENTER);
        y += hX + 2;

        // Strate 2 : la pluie a venir. Trois etats distincts -- bloc absent
        // (on ne sait pas), bloc present sans pluie attendue (`pl == null`,
        // un fait connu, jamais ecrit "0"), bloc present avec pluie
        // attendue. Le pic (`pm`) est desormais sur LA MEME ligne que le
        // delai (une seule ligne SMALL, pas deux) : sur un ecran deux fois
        // plus etroit, la ligne separee du pic ne tient plus a cote de la
        // vigilance et de la consigne -- cf. rapport de tache, budget
        // vertical mesure.
        if (meteo == null) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, "pluie --",
                        Graphics.TEXT_JUSTIFY_CENTER);
            y += hX + 2;
        } else if (meteo["pl"] == null) {
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, "pas de pluie attendue",
                        Graphics.TEXT_JUSTIFY_CENTER);
            y += hX + 2;
        } else {
            // FONT_XTINY (pas FONT_SMALL comme l'ancienne premiere ligne) :
            // fusionner delai et pic sur une seule ligne SMALL debordait
            // horizontalement (mesure a la sonde -- 322 px contre ~270
            // disponibles). XTINY tient largement et libere en plus 12 px de
            // hauteur pour la consigne, le texte le plus important de la
            // page.
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY,
                        "pluie " + meteo["pl"].toString() + " min (pic "
                        + formatMmh(meteo["pm"]) + ")",
                        Graphics.TEXT_JUSTIFY_CENTER);
            y += hX + 2;
        }

        // Strate 3 : la consigne -- le texte le plus important de la page,
        // redige par le mur et affiche tel quel, colore par `cl`. Trois
        // etats : bloc absent (tirets), bloc present sans consigne active
        // (fait connu, "aucune consigne"), bloc present avec consigne
        // (coloree, sur deux lignes). FONT_XTINY, PAS FONT_SMALL (l'ancien
        // choix, garde pour son emphase visuelle) : mesure au device, la
        // corde a cet endroit (277/259 px) ne tient que ~43 caracteres au
        // total en SMALL -- les DEUX consignes reelles les plus longues (52
        // et 59 caracteres) en ressortaient tronquees avant leur verbe
        // d'action alors meme qu'elles COLLAIENT dans l'ecran (aucun
        // debordement geometrique, donc invisible a un test qui ne verifie
        // que les bornes -- cf. rapport de tache, sonde de contenu). En
        // XTINY, la meme corde tient environ 66 caracteres au total,
        // confirme par testCouperConsigneNeTronquePasLesDeuxConsignesReelles
        // (DessinTest.mc) qui rejoue les deux consignes entieres.
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
            var dispo2 = largeurUtile(dc, y + hX - 2);
            var lignes = couperConsigne(dc, meteo["cn"], Graphics.FONT_XTINY,
                                        dispo1, dispo2);
            dc.setColor(couleurNiveau(meteo["cl"]), Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, lignes[0],
                        Graphics.TEXT_JUSTIFY_CENTER);
            if (lignes.size() > 1) {
                dc.drawText(w / 2, y + hX - 2, Graphics.FONT_XTINY, lignes[1],
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
