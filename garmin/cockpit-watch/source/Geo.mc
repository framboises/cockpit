using Toybox.Math;

// Calcul pur : distance et cap entre deux points, et angle d'une fleche
// relative au cap de la montre. Aucune dependance au GPS ni au dessin --
// c'est ce qui rend ces trois formules testables en VALEUR, alors qu'un
// test de dessin ne prouverait que l'absence d'exception.
//
// Ce module n'existe que pour l'app : AUCUNE annotation (:glance) ni
// (:background). La glance et le service de fond ne guident personne.
module Geo {

    const RAYON_TERRE_M = 6371000.0;

    // Distance en metres entre deux points, formule de haversine.
    //
    // Pas de projection plane ici, contrairement au rattachement des alertes
    // cote serveur : celui-la travaille sur quelques centaines de metres
    // autour d'un point connu, celui-ci doit rester juste quelle que soit la
    // distance -- un guidage peut viser un hopital a vingt kilometres.
    function distanceM(lat1, lon1, lat2, lon2) {
        var phi1 = Math.toRadians(lat1);
        var phi2 = Math.toRadians(lat2);
        var dPhi = Math.toRadians(lat2 - lat1);
        var dLambda = Math.toRadians(lon2 - lon1);
        var sinDPhi = Math.sin(dPhi / 2.0);
        var sinDLambda = Math.sin(dLambda / 2.0);
        var a = sinDPhi * sinDPhi
                + Math.cos(phi1) * Math.cos(phi2) * sinDLambda * sinDLambda;
        if (a < 0.0) { a = 0.0; }
        if (a > 1.0) { a = 1.0; }
        return 2.0 * RAYON_TERRE_M * Math.asin(Math.sqrt(a));
    }

    // Cap INITIAL vers le point, en degres depuis le nord vrai [0, 360[.
    //
    // Le cap initial, pas le cap moyen : sur un grand cercle, la direction
    // a suivre change en chemin. C'est la direction a prendre MAINTENANT
    // qui interesse celui qui marche, et c'est aussi ce que calcule la
    // navigation Garmin.
    function capVers(lat1, lon1, lat2, lon2) {
        var phi1 = Math.toRadians(lat1);
        var phi2 = Math.toRadians(lat2);
        var dLambda = Math.toRadians(lon2 - lon1);
        var y = Math.sin(dLambda) * Math.cos(phi2);
        var x = Math.cos(phi1) * Math.sin(phi2)
                - Math.sin(phi1) * Math.cos(phi2) * Math.cos(dLambda);
        return normaliserDegres(Math.toDegrees(Math.atan2(y, x)));
    }

    // Ramene un angle dans [0, 360[. Monkey C, comme beaucoup de langages,
    // ne garantit pas qu'un modulo d'operande negatif reste positif : une
    // fleche a -30 degres se dessinerait a l'envers.
    function normaliserDegres(angle) {
        if (angle == null) { return null; }
        var a = angle;
        while (a < 0.0) { a += 360.0; }
        while (a >= 360.0) { a -= 360.0; }
        return a;
    }

    // Angle de la fleche a l'ecran : la difference entre le cap vers le
    // point et le cap de la montre. Zero = tout droit devant.
    //
    // Rend `null` si l'un des deux manque, et c'est le coeur de la regle
    // de cette page : sans cap de la montre, on connait la direction du
    // point dans le monde mais pas l'orientation du poignet -- dessiner
    // quand meme reviendrait a pointer le nord en pretendant montrer la
    // route. Une fleche qui ment est pire que pas de fleche.
    function angleRelatif(capCible, capMontre) {
        if (capCible == null || capMontre == null) {
            return null;
        }
        return normaliserDegres(capCible - capMontre);
    }

    // "820 m" ou "2.4 km". Le metre exact n'a aucun sens (la precision GPS
    // au poignet se compte en metres) et encombrerait la ligne.
    function formatDistance(metres) {
        if (metres == null) { return Fmt.DASH; }
        if (metres < 1000) {
            return metres.format("%d") + " m";
        }
        return (metres / 1000.0).format("%.1f") + " km";
    }
}
