using Toybox.WatchUi;
using Toybox.Graphics;
using Toybox.Time;

// Trafic : un petit livret a l'interieur de la page, feuillete avec START.
//
//   sous-page 0  bilan  -- verdict global du mur, accidents / bouchons /
//                          dangers en zone, axe le plus degrade
//   sous-pages 1..N     -- TOUS les axes du mur, six par ecran, du plus
//                          degrade au moins degrade
//
// La liste porte desormais un axe par ITINERAIRE Waze (trafic_etat.axes_mur),
// parkings compris, et plus quatre terrains agreges : c'est le meme decoupage
// et la meme severite que le panneau << Axes >> du mur, donc le poignet et
// l'ecran ne peuvent plus afficher deux chiffres differents du meme axe.
//
// Chaque ligne porte les alertes Waze POSEES SUR CET AXE (masque `fl`,
// rattachement geometrique cote serveur) : la page dit desormais sur quel axe
// se trouve l'accident, et plus seulement qu'il y en a un dans le cercle.
//
// Lu depuis Pages.bloc(Cache.loadPages(), "tr") -- cf. watch_pages.build_trafic.
// Contrairement a la main courante, Waze tourne toute l'annee : cette vue n'a
// pas de cas "hors evenement", le serveur construit le bloc meme en mode past.
class TraficView extends WatchUi.View {

    // Six axes par ecran. Mesure au VRAI device (fenix8solar51mm, 280x280) :
    // a hX = 22 px et un pas de 29, la sixieme ligne finit a 225 px, sous le
    // pied de page (241). Une septieme commencerait a 234 et le chevaucherait.
    hidden const AXES_PAR_ECRAN = 6;

    // Masque d'alertes rattachees a l'axe. Miroir EXACT de watch_pages.FL_* :
    // ces valeurs voyagent dans le payload, les deux cotes doivent bouger
    // ensemble.
    hidden const FL_ACCIDENT = 1;
    hidden const FL_BOUCHON = 2;
    hidden const FL_DANGER = 4;

    hidden var mSousPage = 0;

    function initialize() {
        View.initialize();
    }

    // --- Etat du livret ------------------------------------------------
    //
    // Les quatre fonctions ci-dessous sont publiques pour rester testables
    // EN VALEUR (meme raison que FrequentationView.calculDeltaPct) : un
    // livret qui boucle mal, ou qui laisse un index pointer au-dela de la
    // liste apres un rafraichissement qui l'a raccourcie, ne leve aucune
    // exception -- seul un test sur les valeurs le voit.

    function axes() {
        var tr = Pages.bloc(Cache.loadPages(), "tr");
        if (tr == null) { return []; }
        var r = tr["r"];
        return r == null ? [] : r;
    }

    // 1 (le bilan) + un ecran par tranche de six axes. Toujours >= 1 : le
    // bilan existe meme sans aucun axe, il porte le verdict.
    function nbSousPages() {
        var n = axes().size();
        if (n <= 0) { return 1; }
        return 1 + (n + AXES_PAR_ECRAN - 1) / AXES_PAR_ECRAN;
    }

    // Index BORNE a la volee. La liste peut raccourcir entre deux
    // rafraichissements (un axe disparait du releve Waze) : sans ce bornage,
    // l'utilisateur resterait bloque sur un ecran vide sans comprendre
    // pourquoi.
    function sousPage() {
        var n = nbSousPages();
        if (mSousPage >= n) { return n - 1; }
        if (mSousPage < 0) { return 0; }
        return mSousPage;
    }

    function sousPageSuivante() {
        mSousPage = (sousPage() + 1) % nbSousPages();
        WatchUi.requestUpdate();
    }

    // Appelee quand on quitte la page trafic (CockpitView) : revenir au
    // bilan est previsible. Retrouver la page d'axes qu'on avait laissee
    // serait defendable, mais fait perdre le verdict -- la seule chose qui
    // vaille un coup d'oeil rapide.
    function remiseAZero() {
        mSousPage = 0;
    }

    // --- Vocabulaire et couleurs ---------------------------------------

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

    // Severite par axe, 0-4 (trafic_etat.severite_axe, double verrou du mur
    // -- PAS trafic_etat.classify_congestion), echelle distincte du verdict
    // global 0-3. 0 et 1 ("Fluide"/"Dense") sont tous deux sans probleme ->
    // vert ; 4 ("Bouchon") est aussi grave que 3 ("Fort ralenti") au regard
    // de l'action a mener -> meme rouge.
    hidden function couleurSeverite(sev) {
        if (sev == null) { return Graphics.COLOR_DK_GRAY; }
        if (sev >= 4) { return Graphics.COLOR_RED; }
        if (sev == 3) { return Graphics.COLOR_ORANGE; }
        if (sev == 2) { return Graphics.COLOR_YELLOW; }
        return Graphics.COLOR_GREEN;
    }

    // Mots non accentues (comme partout ailleurs dans l'app) repris du MUR
    // (circulation.html:590-601, classify()) -- meme echelle 0-4 que
    // trafic_etat.severite_axe. CE N'EST PAS le vocabulaire de
    // trafic_etat.classify_congestion (normal/charge/sature/bouchon), qui
    // suit une autre echelle. Publique pour rester testable en VALEUR : une
    // derive de vocabulaire ne leve jamais d'exception.
    function statutSeverite(sev) {
        if (sev == null) { return "--"; }
        if (sev >= 4) { return "bouchon"; }
        if (sev == 3) { return "fort ralenti"; }
        if (sev == 2) { return "ralenti"; }
        if (sev == 1) { return "dense"; }
        return "fluide";
    }

    // "i"/"o"/"p"/"-" (watch_pages.build_trafic) -- entrant / sortant /
    // parking / sans direction connue.
    hidden function sensGlyphe(sens) {
        if (sens != null && sens.equals("i")) { return ">"; }
        if (sens != null && sens.equals("o")) { return "<"; }
        if (sens != null && sens.equals("p")) { return "P"; }
        return "-";
    }

    // Badge de la PIRE alerte posee sur l'axe, ou null. Trois lettres et pas
    // une pastille de couleur : sur une ligne d'axe deja dense, la couleur
    // seule ne dirait pas de QUOI il s'agit -- et un daltonien ne
    // distinguerait pas le rouge de l'accident de l'orange du bouchon.
    // Une seule alerte affichee (la plus grave) : la largeur d'une ligne ne
    // permet pas les trois, et c'est la plus grave qui decide de l'action.
    // Publique pour rester testable en VALEUR.
    function badgeAlerte(fl) {
        if (fl == null || fl == 0) { return null; }
        if ((fl & FL_ACCIDENT) != 0) { return "ACC"; }
        if ((fl & FL_BOUCHON) != 0) { return "BOU"; }
        if ((fl & FL_DANGER) != 0) { return "DGR"; }
        return null;
    }

    hidden function couleurBadge(fl) {
        if (fl == null || fl == 0) { return Graphics.COLOR_DK_GRAY; }
        if ((fl & FL_ACCIDENT) != 0) { return Graphics.COLOR_RED; }
        if ((fl & FL_BOUCHON) != 0) { return Graphics.COLOR_ORANGE; }
        return Graphics.COLOR_YELLOW;
    }

    // "3 accidents", "1 bouchon", ou "-- accident" si le compte est INCONNU
    // (alertes perimees ou source en panne, watch_pages/ALERTES_MAX_AGE) --
    // jamais "0", qui se lirait comme un calme operationnel avere. Publique
    // pour rester testable en VALEUR : un retour a "0 accident" ne leverait
    // aucune exception, un test "ne leve pas" ne le detecterait jamais.
    function formatCompte(n, singulier, pluriel) {
        if (n == null) { return Fmt.DASH + " " + singulier; }
        return n.toString() + " " + (n > 1 ? pluriel : singulier);
    }

    // Troncature caractere par caractere jusqu'a rentrer dans `dispo` --
    // meme filet de securite que MeteoView.ajusterTexte / CockpitView.
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

    // --- Rendu ----------------------------------------------------------

    function onUpdate(dc) {
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_BLACK);
        dc.clear();
        var tr = Pages.bloc(Cache.loadPages(), "tr");
        if (sousPage() == 0) {
            dessinerBilan(dc, tr);
        } else {
            dessinerAxes(dc, tr, sousPage() - 1);
        }
        dessinerPied(dc, tr);
    }

    hidden function dessinerBilan(dc, tr) {
        var w = dc.getWidth();
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var hS = dc.getFontHeight(Graphics.FONT_SMALL);
        var hM = dc.getFontHeight(Graphics.FONT_MEDIUM);

        // Bandeau verdict. TOUJOURS rendu, meme si `tr` est absent :
        // couleurVerdict(null) et Pages.verdictMot(null) rendent deja gris
        // fonce / "--" nativement -- la structure de la page ne change pas
        // d'un etat a l'autre, seul son contenu le fait. Pas de couleur
        // affirmative (vert/jaune/orange/rouge) sur un etat qu'on ignore :
        // le gris fonce dit explicitement "inconnu".
        var vd = (tr != null) ? tr["vd"] : null;
        dc.setColor(couleurVerdict(vd), Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 34, Graphics.FONT_MEDIUM, Pages.verdictMot(vd),
                    Graphics.TEXT_JUSTIFY_CENTER);

        // Les trois comptes du mur, un par ligne, chacun colore SEULEMENT
        // s'il est non nul : un zero en couleur d'alerte se lit de loin
        // comme une alerte.
        var y = 34 + hM + 9;
        var pas = hS + 6;
        var ac = (tr != null) ? tr["ac"] : null;
        var jm = (tr != null) ? tr["jm"] : null;
        var hz = (tr != null) ? tr["hz"] : null;

        dessinerCompte(dc, y, hS, formatCompte(ac, "accident", "accidents"),
                       ac, Graphics.COLOR_RED);
        dessinerCompte(dc, y + pas, hS,
                       formatCompte(jm, "bouchon", "bouchons"),
                       jm, Graphics.COLOR_ORANGE);
        dessinerCompte(dc, y + 2 * pas, hS,
                       formatCompte(hz, "danger", "dangers"),
                       hz, Graphics.COLOR_YELLOW);

        // Derniere ligne : ce qui coince le plus, nomme. Sans elle, le
        // verdict resterait sans cause et il faudrait feuilleter pour la
        // trouver.
        var yPire = y + 3 * pas + 4;
        var liste = (tr != null) ? tr["r"] : null;
        var texte;
        var couleur = Graphics.COLOR_DK_GRAY;
        if (tr == null) {
            texte = "source indisponible";
        } else if (liste == null || liste.size() == 0) {
            // Liste vide != panne : `r` porte TOUS les axes du mur sans
            // plancher de severite, donc une liste vide veut dire "aucun axe
            // surveille n'a ete rapporte" -- pas "rien n'est charge".
            texte = "aucun axe rapporte";
        } else {
            var pire = axePireSeverite(liste);
            if (pire[3] == null || pire[3] <= 0) {
                texte = liste.size().toString() + " axes, tous fluides";
            } else {
                texte = pire[0] + " " + Fmt.DASH + " " + statutSeverite(pire[3]);
                couleur = couleurSeverite(pire[3]);
            }
        }
        dc.setColor(couleur, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, yPire, Graphics.FONT_XTINY,
                    ajusterTexte(dc, texte, Graphics.FONT_XTINY,
                                 Pages.largeurUtile(dc, yPire, hX)),
                    Graphics.TEXT_JUSTIFY_CENTER);
    }

    // L'axe le plus DEGRADE, pas le premier de la liste : celle-ci est
    // triee accidents d'abord (watch_pages), donc son premier element peut
    // etre un axe parfaitement fluide sur lequel un accident vient de
    // tomber. Publique pour rester testable en VALEUR.
    function axePireSeverite(liste) {
        var pire = liste[0];
        for (var i = 1; i < liste.size(); i += 1) {
            var sev = liste[i][3];
            var ref = pire[3];
            if (sev != null && (ref == null || sev > ref)) {
                pire = liste[i];
            }
        }
        return pire;
    }

    hidden function dessinerCompte(dc, y, hauteur, texte, valeur, couleurVive) {
        var w = dc.getWidth();
        var couleur = (valeur != null && valeur > 0)
                      ? couleurVive : Graphics.COLOR_DK_GRAY;
        dc.setColor(couleur, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y, Graphics.FONT_SMALL,
                    ajusterTexte(dc, texte, Graphics.FONT_SMALL,
                                 Pages.largeurUtile(dc, y, hauteur)),
                    Graphics.TEXT_JUSTIFY_CENTER);
    }

    hidden function dessinerAxes(dc, tr, ecran) {
        var w = dc.getWidth();
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var liste = (tr != null) ? tr["r"] : null;
        if (liste == null) { liste = []; }

        var debut = ecran * AXES_PAR_ECRAN;
        var fin = debut + AXES_PAR_ECRAN;
        if (fin > liste.size()) { fin = liste.size(); }

        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 26, Graphics.FONT_XTINY,
                    "AXES " + (debut + 1).toString() + "-" + fin.toString()
                    + " / " + liste.size().toString(),
                    Graphics.TEXT_JUSTIFY_CENTER);

        var y = 26 + hX + 12;
        for (var i = debut; i < fin; i += 1) {
            dessinerLigneAxe(dc, y, hX, liste[i]);
            y += hX + 7;
        }
    }

    // Une ligne : glyphe de sens, nom, badge d'alerte, retard, temps.
    //
    //     > Ouest 2      ACC  +7  12'
    //
    // La gravite est portee par le RETARD EN MINUTES, pas seulement par la
    // couleur : c'est le meme chiffre que la colonne `axe-delay` du mur, et
    // il reste lisible pour qui ne distingue pas l'orange du rouge.
    //
    // Le nom est le seul element elastique : il cede la place au reste,
    // jamais l'inverse. Les noms viennent du libelle Waze cote operateur
    // (arbitraire, non controle par cette app) et peuvent depasser toute
    // largeur -- alors qu'un temps tronque serait un chiffre FAUX, pas un
    // mot abrege.
    hidden function dessinerLigneAxe(dc, y, hX, axe) {
        var w = dc.getWidth();
        var font = Graphics.FONT_XTINY;

        // 8 px de marge interne de chaque cote : coller le texte au verre
        // rond le rend illisible sur une lunette biseautee.
        var dispo = Pages.largeurUtile(dc, y, hX) - 16;
        if (dispo < 40) { return; }
        var xG = w / 2.0 - dispo / 2.0;
        var xD = w / 2.0 + dispo / 2.0;

        var sev = axe[3];
        var couleur = couleurSeverite(sev);

        // Temps de parcours, ancre a droite.
        var temps = (axe[2] != null) ? (axe[2].toString() + "'") : (Fmt.DASH);
        dc.setColor(couleur, Graphics.COLOR_TRANSPARENT);
        dc.drawText(xD, y, font, temps, Graphics.TEXT_JUSTIFY_RIGHT);
        var xCurseur = xD - dc.getTextWidthInPixels(temps, font) - 6;

        // Retard, juste a gauche du temps. Affiche seulement s'il y en a un :
        // un "+0" sur chaque ligne fluide n'apporterait rien et mangerait la
        // place du nom.
        var retard = (axe.size() > 5 && axe[5] != null) ? axe[5] : 0;
        if (retard > 0) {
            var txtRetard = "+" + retard.toString();
            dc.drawText(xCurseur, y, font, txtRetard,
                        Graphics.TEXT_JUSTIFY_RIGHT);
            xCurseur -= dc.getTextWidthInPixels(txtRetard, font) + 6;
        }

        // Badge d'alerte rattachee a CET axe.
        var fl = (axe.size() > 4) ? axe[4] : null;
        var badge = badgeAlerte(fl);
        if (badge != null) {
            dc.setColor(couleurBadge(fl), Graphics.COLOR_TRANSPARENT);
            dc.drawText(xCurseur, y, font, badge, Graphics.TEXT_JUSTIFY_RIGHT);
            xCurseur -= dc.getTextWidthInPixels(badge, font) + 6;
        }

        // Glyphe de sens, ancre a gauche.
        var glyphe = sensGlyphe(axe[1]);
        dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(xG, y, font, glyphe, Graphics.TEXT_JUSTIFY_LEFT);
        var xNom = xG + dc.getTextWidthInPixels(glyphe, font) + 6;

        // Le nom prend ce qui reste, et rien de plus.
        var place = xCurseur - xNom;
        if (place <= 0) { return; }
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(xNom, y, font,
                    ajusterTexte(dc, axe[0], font, place),
                    Graphics.TEXT_JUSTIFY_LEFT);
    }

    // Pied commun aux sous-pages : age du bloc et position dans le livret.
    // Le compteur de sous-pages est ce qui dit qu'il Y A quelque chose
    // apres -- sans lui, rien n'indique que START feuillette.
    hidden function dessinerPied(dc, tr) {
        var w = dc.getWidth();
        var hX = dc.getFontHeight(Graphics.FONT_XTINY);
        var yFoot = dc.getHeight() - hX - 17;
        var now = Time.now().value();
        var age = (tr != null && tr["t"] != null) ? (now - tr["t"]) : null;
        var texte = "maj " + Fmt.age(age);
        var total = nbSousPages();
        if (total > 1) {
            texte += "  " + (sousPage() + 1).toString() + "/"
                     + total.toString();
        }
        dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, yFoot, Graphics.FONT_XTINY,
                    ajusterTexte(dc, texte, Graphics.FONT_XTINY,
                                 Pages.largeurUtile(dc, yFoot, hX)),
                    Graphics.TEXT_JUSTIFY_CENTER);
    }
}
