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
}
