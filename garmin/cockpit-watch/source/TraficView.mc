using Toybox.WatchUi;
using Toybox.Graphics;
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

    // "3 alertes . 1 accident", ou tirets si le compte est INCONNU (perime
    // ou source en panne, watch_pages.build_trafic/ALERTES_MAX_AGE) --
    // jamais "0", qui se lirait comme un calme operationnel avere. Publique
    // (meme raison que FrequentationView.calculDeltaPct) pour rester
    // testable en VALEUR : un retour a l'ancien "0 alerte . 0 accident" ne
    // leverait aucune exception, un test "ne leve pas" ne le detecterait
    // donc jamais.
    function formatComptes(z, ac) {
        var zTxt = (z != null)
                   ? (z.toString() + (z > 1 ? " alertes" : " alerte"))
                   : (Fmt.DASH + " alerte");
        var acTxt = (ac != null)
                   ? (ac.toString() + (ac > 1 ? " accidents" : " accident"))
                   : (Fmt.DASH + " accident");
        return zTxt + " . " + acTxt;
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

    // Severite par terrain, 0-4 (trafic_etat.severite_axe, double verrou du
    // mur -- PAS trafic_etat.classify_congestion), echelle distincte du
    // verdict global 0-3. 0 et 1 ("Fluide"/"Dense") sont tous deux sans
    // probleme -> vert ; 4 ("Bouchon") est aussi grave que 3
    // ("Fort ralenti") au regard de l'action a mener -> meme rouge.
    hidden function couleurSeverite(sev) {
        if (sev == null) { return Graphics.COLOR_DK_GRAY; }
        if (sev >= 4) { return Graphics.COLOR_RED; }
        if (sev == 3) { return Graphics.COLOR_ORANGE; }
        if (sev == 2) { return Graphics.COLOR_YELLOW; }
        return Graphics.COLOR_GREEN;
    }

    // Mots non accentues (comme partout ailleurs dans l'app) repris du MUR
    // (circulation.html:590-601, classify()) -- meme echelle 0-4 que
    // trafic_etat.severite_axe, qui alimente t[3] ici. CE N'EST PAS le
    // vocabulaire de trafic_etat.classify_congestion (normal/charge/
    // sature/bouchon) : cette vue affichait ces mots-la contre un nombre
    // qui vient desormais de severite_axe (double verrou du mur), une
    // echelle differente -- pire cas trouve, severite 1 : le mur dit
    // "Dense", l'ancien vocabulaire ici disait "normal". Publique (meme
    // raison que FrequentationView.calculDeltaPct) pour rester testable en
    // VALEUR : une derive de vocabulaire ne leve jamais d'exception.
    function statutSeverite(sev) {
        if (sev == null) { return "--"; }
        if (sev >= 4) { return "bouchon"; }
        if (sev == 3) { return "fort ralenti"; }
        if (sev == 2) { return "ralenti"; }
        if (sev == 1) { return "dense"; }
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
        return ajusterTexte(dc, nom, Graphics.FONT_SMALL, dispo);
    }

    // Troncature caractere par caractere jusqu'a rentrer dans `dispo` --
    // meme filet de securite que MeteoView.ajusterTexte/CockpitView.
    // ajusterTexte, generalise ici (font en parametre) pour couvrir aussi
    // la ligne de comptes (FONT_XTINY), qui n'a pas de largeur bornee cote
    // serveur.
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

    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();
        var w = dc.getWidth();
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var hS = dc.getFontHeight(Graphics.FONT_SMALL);
        var hM = dc.getFontHeight(Graphics.FONT_MEDIUM);

        var tr = Pages.bloc(Cache.loadPages(), "tr");

        // Bandeau verdict : moitie haute, sommet contraint -- corde tres
        // au-dessus du mot le plus long ("VIGILANCE") en FONT_MEDIUM.
        // Toujours rendu, meme si
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

        // Resserre a 8 (etait 16) : la ligne de comptes qui suit les
        // terrains a besoin de la corde la plus large possible a son
        // ordonnee (la BASE de son bloc, pas son ancre, contraint -- cf.
        // Pages.largeurUtile) ; la remonter de 8 px gagne ~15 px de corde a
        // cet endroit, cf. rapport de tache.
        y += hM + 8;

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

        // Le serveur envoie jusqu'a quatre terrains (MAX_TERRAINS,
        // watch_pages.py), mais seuls DEUX tiennent entre le bandeau et le
        // pied sur un ecran de 280 (mesure au device -- le 3e chevauchait
        // deja le pied, le 4e etait entierement hors ecran). Partage avec
        // le calcul de `resteTerrains` plus bas, pour ne jamais desaccorder
        // le nombre affiche et le nombre annonce comme masque.
        var maxTerrains = 2;

        if (terrains.size() == 0) {
            // Liste vide != panne : `r` porte les quatre terrains les plus
            // charges SANS plancher de severite (watch_pages.build_trafic),
            // donc une liste vide veut dire "aucun axe surveille n'a ete
            // rapporte" -- pas "rien n'est charge", que le libelle precedent
            // laissait entendre a tort.
            dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
            dc.drawText(w / 2, y, Graphics.FONT_SMALL, "aucun axe rapporte",
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
            //
            // Le reste n'est pas jete : le nombre masque est ajoute a la
            // ligne de comptes plus bas ("+N axes").
            var nTerrains = terrains.size() > maxTerrains ? maxTerrains
                                                            : terrains.size();
            for (var i = 0; i < nTerrains; i += 1) {
                var t = terrains[i];

                var dispoNom = Pages.largeurUtile(dc, y, hS);
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
        // `z`/`ac` valent desormais reellement `null` quand le collecteur
        // d'alertes est en panne ou perime (watch_pages.build_trafic,
        // ALERTES_MAX_AGE) : un compte inconnu doit rendre "--", jamais
        // "0" -- ce meme fichier le rend deja plus haut (lignes ~125-126)
        // pour le cas bloc entier absent, ce cas-ci (bloc present, compte
        // alertes seul inconnu) doit se peindre pareil. formatComptes est
        // testee en valeur a part (DessinTest.mc).
        var texteComptes = formatComptes(tr["z"], tr["ac"]);
        // Terrains masques par la limite d'affichage (pas les terrains
        // absents du payload -- ceux-la, watch_pages ne les envoie deja
        // plus) : ajoutes en toutes lettres, jamais tus. Le filet de
        // securite qui suit doit donc tronquer le COMPTE si la ligne
        // deborde, jamais la mention "+N axes" -- sinon la mention
        // disparaitrait exactement dans le cas ou elle compte le plus (deux
        // terrains masques ET des comptes eleves).
        var resteTerrains = terrains.size() -
                             (terrains.size() > maxTerrains ? maxTerrains
                                                             : terrains.size());
        var suffixeReste = "";
        if (resteTerrains > 0) {
            suffixeReste = "  +" + resteTerrains.toString()
                           + (resteTerrains > 1 ? " axes" : " axe");
        }
        // Filet de securite : compte a deux chiffres + accidents peut
        // depasser la corde (la plus etroite de la page, cf.
        // Pages.largeurUtile) dans le pire cas plausible -- tronquer le
        // compte plutot que deborder du verre rond, en reservant toujours
        // la place du suffixe "+N axes".
        var dispoComptes = Pages.largeurUtile(dc, y, hX);
        var dispoBase = dispoComptes -
                         dc.getTextWidthInPixels(suffixeReste, Graphics.FONT_XTINY);
        texteComptes = ajusterTexte(dc, texteComptes, Graphics.FONT_XTINY,
                                    dispoBase) + suffixeReste;
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
