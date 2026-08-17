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

    // QUATRE axes par ecran, DEUX lignes chacun. Mesure au VRAI device
    // (fenix8solar51mm, 280x280) : premiere ligne a y = 58 + 46*i, seconde a
    // +19 px ; le quatrieme axe finit a 237, juste sous le pied (241).
    //
    // Six axes sur une ligne chacun tenaient auparavant -- mais avec un
    // format de temps ambigu ("24'"). Le format du bloc << Temps d'acces >>
    // du cockpit ("4m 20s" + "+45s") demande 176 px a lui seul dans son
    // pire cas, sur une corde utile de 186 : il ne reste rien pour le nom.
    // D'ou la seconde ligne. Deux axes de moins par ecran, mais des chiffres
    // qu'on n'a plus a deviner.
    hidden const AXES_PAR_ECRAN = 4;

    // Ordonnee de la premiere ligne d'un axe, et pas entre deux axes.
    hidden const AXE_Y0 = 58;
    hidden const AXE_PAS = 46;

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

    // DIRECTION de l'axe : "i" entrant, "o" sortant. Port de dirLabel
    // (static/js/traffic.js:52), qui rend ENTREE / SORTIE ou RIEN.
    //
    // Trois lettres et non une fleche : ">" seul demande d'etre interprete,
    // et les formes longues (56 a 72 px) ne tiennent pas sur une ligne deja
    // chargee. ENT / SOR font 31 et 32 px.
    //
    // ⚠️ UN AXE SANS DIRECTION REND UNE CHAINE VIDE, PAS UN TIRET. Le tiret
    // signifie << valeur inconnue >> partout ailleurs dans cette app ; ici
    // il n'y a rien a connaitre. Les parkings et aires d'accueil (tag `##`,
    // transporte "-") et les autoroutes (tag `#P`, transporte "p") n'ont pas
    // de sens de circulation : le cockpit laisse la colonne vide pour eux,
    // et c'est exact.
    //
    // ⚠️ NE JAMAIS ECRIRE "PKG" POUR "p". Le tag `#P` de Waze designe les
    // AUTOROUTES (A28, A11 -- verifie sur le releve reel), pas les parkings.
    // Le cockpit lui accole une icone `fork_right`, une bifurcation
    // d'autoroute (tagLabel, static/js/traffic.js:58), et son onglet
    // << Autoroutes >> filtre precisement sur `tag === "P"` tandis que son
    // onglet << Parkings >> filtre sur `category === "pkg_aa" && tag !== "P"`.
    // La premiere version de cette vue affichait "PKG" sur les autoroutes et
    // "--" sur les parkings : les deux etaient faux, en sens inverse.
    //
    // Le TYPE d'axe n'est donc pas rendu ici : le nom le porte deja ("A28"
    // est manifestement une autoroute, "Panorama" un parking), et la montre
    // n'a pas d'onglet de filtrage a alimenter contrairement au cockpit.
    // Publique pour rester testable en VALEUR.
    function sensLibelle(sens) {
        if (sens != null && sens.equals("i")) { return "ENT"; }
        if (sens != null && sens.equals("o")) { return "SOR"; }
        return "";
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
        Pages.dessinerPagination(dc, sousPage(), nbSousPages());
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

        // "AXES 7-12 / 13", mais "AXES 13 / 13" quand la derniere sous-page
        // n'en porte qu'un : "AXES 13-13" se lit comme une erreur
        // d'affichage. Mesure sur les 13 axes reels du releve de prod, ou
        // c'est exactement le cas de la derniere page.
        var plage = (fin - debut > 1)
                    ? ((debut + 1).toString() + "-" + fin.toString())
                    : fin.toString();
        dc.setColor(Graphics.COLOR_LT_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, 26, Graphics.FONT_XTINY,
                    "AXES " + plage + " / " + liste.size().toString(),
                    Graphics.TEXT_JUSTIFY_CENTER);

        for (var i = debut; i < fin; i += 1) {
            dessinerAxe(dc, AXE_Y0 + AXE_PAS * (i - debut), hX, liste[i]);
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
    // Un axe, DEUX lignes -- structure du bloc << Temps d'acces >> du
    // cockpit (static/js/traffic.js:createRow), ramenee a la corde d'un
    // cadran de 280 px :
    //
    //     Antares Sud                    ACC
    //     ENT        4m 20s             +45s
    //
    // Ligne 1 : le nom (elastique) et le badge de la pire alerte posee sur
    // cet axe. Ligne 2 : le sens, le temps de parcours, le retard.
    //
    // Le RETARD est toujours affiche, "+0s" compris, comme le fait le
    // cockpit : c'est ce qui distingue un axe fluide (retard connu, nul)
    // d'un axe dont on ignore le retard. L'ancienne version le masquait a
    // zero, et les deux se ressemblaient.
    //
    // Le TEMPS est en "4m 20s" et non "24'" : sur des trajets allant de
    // quelques dizaines de secondes a une demi-heure, une unite implicite
    // se devine mal.
    hidden function dessinerAxe(dc, y, hX, axe) {
        var w = dc.getWidth();
        var font = Graphics.FONT_XTINY;
        var sev = axe[3];
        var couleur = couleurSeverite(sev);

        // --- Ligne 1 : nom + badge d'alerte ---
        //
        // 8 px de marge interne de chaque cote : coller le texte au verre
        // rond le rend illisible sur une lunette biseautee.
        var dispo1 = Pages.largeurUtile(dc, y, hX) - 16;
        if (dispo1 < 40) { return; }
        var xG1 = w / 2.0 - dispo1 / 2.0;
        var xD1 = w / 2.0 + dispo1 / 2.0;

        var placeNom = dispo1;
        var fl = (axe.size() > 4) ? axe[4] : null;
        var badge = badgeAlerte(fl);
        if (badge != null) {
            dc.setColor(couleurBadge(fl), Graphics.COLOR_TRANSPARENT);
            dc.drawText(xD1, y, font, badge, Graphics.TEXT_JUSTIFY_RIGHT);
            placeNom -= dc.getTextWidthInPixels(badge, font) + 6;
        }
        if (placeNom > 0) {
            dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
            dc.drawText(xG1, y, font,
                        ajusterTexte(dc, axe[0], font, placeNom),
                        Graphics.TEXT_JUSTIFY_LEFT);
        }

        // --- Ligne 2 : sens, temps, retard ---
        //
        // Le nom de la ligne 1 est le seul element elastique de l'axe : ici,
        // rien ne cede. Un temps ou un retard tronque serait un chiffre
        // FAUX, pas un mot abrege -- et rien dans un controle geometrique ne
        // distingue les deux.
        var y2 = y + hX - 3;
        var dispo2 = Pages.largeurUtile(dc, y2, hX) - 16;
        if (dispo2 < 40) { return; }
        var xG2 = w / 2.0 - dispo2 / 2.0;
        var xD2 = w / 2.0 + dispo2 / 2.0;

        // Retard a droite, colore par la severite : c'est lui qui dit si
        // l'axe se degrade, le temps seul ne le dit pas (un trajet long
        // peut etre parfaitement normal).
        var retard = (axe.size() > 5) ? axe[5] : null;
        dc.setColor(couleur, Graphics.COLOR_TRANSPARENT);
        dc.drawText(xD2, y2, font, Fmt.retard(retard),
                    Graphics.TEXT_JUSTIFY_RIGHT);

        // Sens a gauche, en gris : c'est un reperage, pas une mesure.
        dc.setColor(Graphics.COLOR_DK_GRAY, Graphics.COLOR_TRANSPARENT);
        dc.drawText(xG2, y2, font, sensLibelle(axe[1]),
                    Graphics.TEXT_JUSTIFY_LEFT);

        // Temps au centre, en blanc : la valeur de reference, celle qu'on
        // vient chercher.
        dc.setColor(Graphics.COLOR_WHITE, Graphics.COLOR_TRANSPARENT);
        dc.drawText(w / 2, y2, font, Fmt.duree(axe[2]),
                    Graphics.TEXT_JUSTIFY_CENTER);
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
