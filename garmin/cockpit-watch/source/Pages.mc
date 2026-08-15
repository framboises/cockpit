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
}
