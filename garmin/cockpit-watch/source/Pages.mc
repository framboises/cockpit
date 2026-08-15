using Toybox.Math;

// Ce module n'existe que pour l'app : AUCUNE annotation (:glance) ni
// (:background) ici. C'est ce qui garantit que les quatre blocs (mc/tr/me/st)
// et le code qui les lit ne sont jamais compiles dans la glance ni le
// service de fond, cf. Api.toPagesDict et Cache.savePages/loadPages.
module Pages {

    // Les mots du verdict trafic sont ceux du mur (circulation.html:494) :
    // le poignet et l'ecran doivent dire la meme chose du meme etat.
    function verdictMot(vd) {
        if (vd == null) { return "--"; }
        if (vd >= 3) { return "CRITIQUE"; }
        if (vd == 2) { return "TENSION"; }
        if (vd == 1) { return "VIGILANCE"; }
        return "FLUIDE";
    }

    // Un seul bloc peut valoir null (source tombee cote serveur), et pg
    // lui-meme peut valoir null (aucun cache pages disponible). Les quatre
    // accesseurs ci-dessous ne font que nommer la cle : ils ne restructurent
    // rien, les vues qui consommeront ces blocs n'existent pas encore.
    function bloc(pg, cle) {
        if (pg == null) { return null; }
        return pg[cle];
    }

    function mainCourante(pg) { return bloc(pg, "mc"); }
    function trafic(pg) { return bloc(pg, "tr"); }
    function meteo(pg) { return bloc(pg, "me"); }
    function frequentation(pg) { return bloc(pg, "st"); }

    // Vigilance Meteo-France (`vg`, echelle 0-3 de meteo_etat.ORDRE_COULEURS
    // -- vert/jaune/orange/rouge), DISTINCTE de la consigne (`cn`/`cl`, seuils
    // du mur) et du verdict trafic (`vd`, mots du mur circulation) : les
    // trois echelles partagent 0-3 mais ne se melangent jamais. `null` quand
    // le niveau est inconnu OU vert -- dans les deux cas, rien a nommer, la
    // regle commune a toute l'app est de rester muet plutot que d'afficher
    // un calme qui n'est pas confirme (inconnu) ou qui ne merite aucune
    // mention (vert). Le mot d'abord, la couleur en renfort (couleurNiveau
    // dans MeteoView/CockpitView) : jamais la couleur seule, un daltonien en
    // plein soleil ne la distinguerait pas.
    function vigilanceMot(vg) {
        if (vg == null || vg < 1) { return null; }
        if (vg >= 3) { return "VIGILANCE ROUGE"; }
        if (vg == 2) { return "VIGILANCE ORANGE"; }
        return "VIGILANCE JAUNE";
    }

    // Forme compacte pour la bande de voyants (page 1, CockpitView) : elle
    // partage sa ligne avec << MC n >> et le verdict trafic, pas de place
    // pour la forme longue. Meme echelle, memes seuils que vigilanceMot.
    function vigilanceMotCourt(vg) {
        if (vg == null || vg < 1) { return null; }
        if (vg >= 3) { return "VIG ROUGE"; }
        if (vg == 2) { return "VIG ORANGE"; }
        return "VIG JAUNE";
    }

    // Largeur utile du cadran rond a la hauteur d'un BLOC, pas d'un point.
    // Un texte est ancre par le HAUT (jamais TEXT_JUSTIFY_VCENTER dans cette
    // app) et occupe [y, y + hauteur] : sur un cadran rond, la corde
    // disponible RETRECIT en s'eloignant du centre (140 sur un ecran de
    // 280), donc c'est le bord du bloc le PLUS ELOIGNE du centre qui
    // contraint, jamais l'ancre seule.
    //
    // Chaque vue appelait auparavant largeurUtile(dc, y) -- la corde a
    // L'ANCRE, pas au bord contraignant -- et SURESTIMAIT donc la place
    // disponible chaque fois que le bloc s'etendait vers le centre plutot
    // que vers le bord (ex : une ligne juste sous le centre, dont la BASE,
    // pas le sommet, est la plus proche du bord oppose). Exactement le
    // meme defaut que la reprise verticale de cette tache a corrige sur
    // l'axe des ordonnees -- ici sur l'axe des largeurs. Ecarts mesures
    // avant correction, au pire cas de chaque vue : FrequentationView
    // -35,9 px, MeteoView -17,7 et -10,4 px, TraficView -15,2 px (cf.
    // rapport de tache).
    //
    // Centralisee ici (auparavant dupliquee, identique, dans les six vues)
    // pour n'avoir qu'UNE formule a corriger si le rayon ou la convention
    // d'ancrage changent un jour.
    function largeurUtile(dc, y, hauteur) {
        var r = dc.getWidth() / 2.0;
        var dyHaut = (y - r).abs();
        var dyBas = (y + hauteur - r).abs();
        var dy = dyHaut > dyBas ? dyHaut : dyBas;
        if (dy >= r) {
            return 0.0;
        }
        return 2.0 * Math.sqrt(r * r - dy * dy);
    }
}
