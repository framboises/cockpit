using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.Math;
using Toybox.Time;

// Trafic : verdict global du mur circulation, terrains les plus charges,
// comptes d'alertes/accidents dans le geofence du circuit. Lu depuis
// Pages.bloc(Cache.loadPages(), "tr") -- cf. watch_pages.build_trafic.
// Contrairement a la main courante, Waze tourne toute l'annee : cette vue
// n'a pas de cas "hors evenement", le serveur construit le bloc meme en
// mode past.
class TraficView extends WatchUi.View {

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

    // Les mots et couleurs du verdict sont ceux du mur (circulation.html:494),
    // le mot vient de Pages.verdictMot -- la couleur seule ne porte jamais
    // une identite (daltonisme, ecran plein soleil), les deux doivent
    // toujours etre poses ensemble.
    hidden function couleurVerdict(vd) {
        if (vd == null) { return Graphics.COLOR_DK_GRAY; }
        if (vd >= 3) { return Graphics.COLOR_RED; }
        if (vd == 2) { return Graphics.COLOR_ORANGE; }
        if (vd == 1) { return Graphics.COLOR_YELLOW; }
        return Graphics.COLOR_GREEN;
    }

    // Severite par terrain, 0-4 (trafic_etat.classify_congestion), echelle
    // distincte du verdict global 0-3. 0 et 1 ("plus fluide"/"normal") sont
    // tous deux sans probleme -> vert ; 4 ("bouchon") est aussi grave que 3
    // ("sature") au regard de l'action a mener -> meme rouge.
    hidden function couleurSeverite(sev) {
        if (sev == null) { return Graphics.COLOR_DK_GRAY; }
        if (sev >= 4) { return Graphics.COLOR_RED; }
        if (sev == 3) { return Graphics.COLOR_ORANGE; }
        if (sev == 2) { return Graphics.COLOR_YELLOW; }
        return Graphics.COLOR_GREEN;
    }

    // Mots non accentues (comme partout ailleurs dans l'app) repris de
    // trafic_etat.classify_congestion.
    hidden function statutSeverite(sev) {
        if (sev == null) { return "--"; }
        if (sev >= 4) { return "bouchon"; }
        if (sev == 3) { return "sature"; }
        if (sev == 2) { return "charge"; }
        if (sev == 1) { return "normal"; }
        return "fluide";
    }

    // "i"/"o"/"-" (watch_pages.build_trafic) -- boitier entrant / sortant /
    // sans direction connue.
    hidden function sensFleche(sens) {
        if (sens != null && sens.equals("i")) { return ">"; }
        if (sens != null && sens.equals("o")) { return "<"; }
        return "-";
    }

    // Les noms de terrain viennent du libelle Waze cote operateur (arbitraire,
    // pas controle par cette app) : cf. test_watch_state.py::
    // test_taille_du_payload_complet_sous_deux_ko, ou "Rond point Maison
    // Blanche" (26 caracteres) mesure 313 px en FONT_SMALL -- deja proche de
    // la corde la plus etroite du bloc terrains (360 px a y=89). Filet de
    // securite au-dela de cette marge mesuree : tronquer plutot que deborder.
    hidden function ajusterNom(dc, nom, dispo) {
        if (dc.getTextWidthInPixels(nom, Graphics.FONT_SMALL) <= dispo) {
            return nom;
        }
        var texte = nom;
        while (texte.length() > 1 &&
               dc.getTextWidthInPixels(texte, Graphics.FONT_SMALL) > dispo) {
            texte = texte.substring(0, texte.length() - 1);
        }
        return texte;
    }

    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();
        var w = dc.getWidth();
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var hS = dc.getFontHeight(Graphics.FONT_SMALL);
        var hM = dc.getFontHeight(Graphics.FONT_MEDIUM);

        var tr = Pages.bloc(Cache.loadPages(), "tr");

        // Bandeau verdict : moitie haute (y=34 < 227), c'est son sommet qui
        // contraint -- cf. sonde, largeurUtile(dc, 34) tres au-dessus du mot
        // le plus long ("VIGILANCE") en FONT_MEDIUM. Toujours rendu, meme si
        // `tr` est absent : couleurVerdict(null) et Pages.verdictMot(null)
        // rendent deja gris fonce / "--" nativement -- la structure de la
        // page ne change pas d'un etat a l'autre, seul son contenu le fait.
        // Pas de couleur affirmative (vert/jaune/orange/rouge) sur un etat
        // qu'on ignore : le gris fonce dit explicitement "inconnu".
        var y = 34;
        var vd = (tr != null) ? tr["vd"] : null;
        dc.setColor(couleurVerdict(vd), Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_MEDIUM, Pages.verdictMot(vd),
                    Graphics.TEXT_JUSTIFY_CENTER);

        y += hM + 16;

        if (tr == null) {
            // Bloc source en panne : on ne sait PAS combien de terrains il y
            // aurait, donc on n'en invente aucune ligne (regle commune aux
            // quatre pages) -- seuls les deux champs fixes de cette page
            // (bandeau deja rendu ci-dessus, comptes ci-dessous) passent en
            // tirets, meme ordonnee que le message "aucun axe charge" du cas
            // terrains vides pour garder la meme structure -- seul le
            // contenu (et le sens, absence vs zero connu) differe.
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_XTINY, "-- alerte . -- accident",
                        Graphics.TEXT_JUSTIFY_CENTER);

            var yFootAbsent = dc.getHeight() - hX - 17;
            dc.setColor(Graphics.COLOR_RED, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, yFootAbsent, Graphics.FONT_XTINY, "indisponible",
                        Graphics.TEXT_JUSTIFY_CENTER);
            return;
        }

        var terrains = tr["r"];
        if (terrains == null) { terrains = []; }

        if (terrains.size() == 0) {
            // Liste vide != panne : aucun axe surveille n'est charge en ce
            // moment, ce n'est jamais un blanc silencieux.
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_SMALL, "aucun axe charge",
                        Graphics.TEXT_JUSTIFY_CENTER);
            y += hS + 10;
        } else {
            // Deja tries par gravite decroissante cote serveur (watch_pages
            // .build_trafic) : ne pas retrier, l'ordre porte l'intention
            // (montrer d'abord ce qui coince).
            //
            // Deux lignes par terrain (nom centre, puis sens/minutes/statut
            // centre) plutot qu'une ligne coupee en deux colonnes : les noms
            // sont des libelles Waze arbitraires, potentiellement longs
            // (cf. ajusterNom), une seule ligne pleine largeur leur laisse
            // toute la corde disponible plutot que la moitie.
            for (var i = 0; i < terrains.size(); i += 1) {
                var t = terrains[i];

                var dispoNom = largeurUtile(dc, y);
                var nom = ajusterNom(dc, t[0], dispoNom);
                dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
                dc.drawText(w / 2, y, Graphics.FONT_SMALL, nom,
                            Graphics.TEXT_JUSTIFY_CENTER);

                var yDetail = y + hS - 2;
                var detail = sensFleche(t[1]) + " " + t[2].toString() + " min "
                             + statutSeverite(t[3]);
                dc.setColor(couleurSeverite(t[3]), Graphics.COLOR_TRANSPARENT);
                dc.drawText(w / 2, yDetail, Graphics.FONT_XTINY, detail,
                            Graphics.TEXT_JUSTIFY_CENTER);

                y = yDetail + hX + 10;
            }
        }

        // Comptes d'alertes/accidents Waze dans le geofence du circuit.
        var z = (tr["z"] != null) ? tr["z"] : 0;
        var ac = (tr["ac"] != null) ? tr["ac"] : 0;
        var texteComptes = z.toString() + (z > 1 ? " alertes" : " alerte")
                            + " . " + ac.toString()
                            + (ac > 1 ? " accidents" : " accident");
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_XTINY, texteComptes,
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Pied de page : age du bloc. Moitie basse, meme position que les
        // autres vues -- cf. sonde, sa BASE contraint et reste tres au-dessus
        // du besoin des deux libelles possibles.
        var yFoot = dc.getHeight() - hX - 17;
        var now = Time.now().value();
        var age = (tr["t"] != null) ? (now - tr["t"]) : null;
        dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, yFoot, Graphics.FONT_XTINY, "maj " + Fmt.age(age),
                    Graphics.TEXT_JUSTIFY_CENTER);
    }
}
