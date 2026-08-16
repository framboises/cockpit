using Toybox.Test;

// Les valeurs attendues ci-dessous sont calculees a la main sur des cas ou
// la geometrie est evidente (meridien, parallele, points connus du circuit),
// PAS obtenues en executant le code teste. Un test qui recopie la sortie de
// l'implementation ne prouve que sa stabilite, jamais sa justesse.

// Un degre de latitude vaut 111,19 km sur une sphere de rayon 6371 km
// (2 pi R / 360). Toutes les attentes en distance en decoulent.
const DEG_LAT_M = 111194.9;

(:test)
function testDistanceNulleSurUnMemePoint(logger) {
    Test.assert(Geo.distanceM(47.95, 0.22, 47.95, 0.22) < 0.001);
    return true;
}

(:test)
function testDistanceUnDegreDeLatitude(logger) {
    // Plein nord : la distance ne depend pas de la longitude.
    var d = Geo.distanceM(47.0, 0.22, 48.0, 0.22);
    Test.assert(d > DEG_LAT_M - 50.0);
    Test.assert(d < DEG_LAT_M + 50.0);
    return true;
}

(:test)
function testDistanceCourteEnMetres(logger) {
    // 0,001 degre de latitude = ~111,2 m. Ordre de grandeur d'un guidage
    // reel a l'interieur du circuit.
    var d = Geo.distanceM(47.95, 0.22, 47.951, 0.22);
    Test.assert(d > 108.0);
    Test.assert(d < 114.0);
    return true;
}

(:test)
function testDistanceSymetrique(logger) {
    var ab = Geo.distanceM(47.95, 0.22, 47.97, 0.25);
    var ba = Geo.distanceM(47.97, 0.25, 47.95, 0.22);
    Test.assert((ab - ba).abs() < 0.01);
    return true;
}

(:test)
function testDistanceGrandeEchelleResteJuste(logger) {
    // Le Mans -> Paris, ~185 km a vol d'oiseau. Une projection plane (celle
    // qu'utilise le serveur pour rattacher les alertes sur quelques
    // centaines de metres) derangerait nettement a cette distance : cette
    // page-ci peut viser un hopital loin du circuit.
    var d = Geo.distanceM(47.995, 0.196, 48.857, 2.352);
    Test.assert(d > 180000.0);
    Test.assert(d < 190000.0);
    return true;
}

(:test)
function testCapPleinNord(logger) {
    Test.assert(Geo.capVers(47.95, 0.22, 47.96, 0.22).abs() < 0.01);
    return true;
}

(:test)
function testCapPleinSudEstCentQuatreVingts(logger) {
    var cap = Geo.capVers(47.95, 0.22, 47.94, 0.22);
    Test.assert((cap - 180.0).abs() < 0.01);
    return true;
}

(:test)
function testCapPleinEstEstQuatreVingtDix(logger) {
    // Sur un grand cercle, le cap initial vers un point plein est n'est
    // exactement 90 degres que si les deux points sont sur l'equateur ; a
    // notre latitude il s'en ecarte un peu, mais reste tres proche pour un
    // ecart de longitude aussi faible.
    var cap = Geo.capVers(47.95, 0.22, 47.95, 0.23);
    Test.assert((cap - 90.0).abs() < 0.5);
    return true;
}

(:test)
function testCapPleinOuestEstDeuxCentSoixanteDix(logger) {
    // LE cas que la normalisation existe pour tenir : atan2 rend ici un
    // angle NEGATIF (-90). Sans normalisation, la fleche se dessinerait
    // a l'oppose.
    var cap = Geo.capVers(47.95, 0.22, 47.95, 0.21);
    Test.assert((cap - 270.0).abs() < 0.5);
    return true;
}

(:test)
function testCapToujoursDansZeroTroisCentSoixante(logger) {
    var points = [[47.96, 0.22], [47.94, 0.22], [47.95, 0.23], [47.95, 0.21],
                  [47.96, 0.21], [47.94, 0.23], [47.96, 0.23], [47.94, 0.21]];
    for (var i = 0; i < points.size(); i += 1) {
        var cap = Geo.capVers(47.95, 0.22, points[i][0], points[i][1]);
        Test.assert(cap >= 0.0);
        Test.assert(cap < 360.0);
    }
    return true;
}

(:test)
function testNormaliserRameneLesNegatifs(logger) {
    Test.assert((Geo.normaliserDegres(-90.0) - 270.0).abs() < 0.001);
    Test.assert((Geo.normaliserDegres(-370.0) - 350.0).abs() < 0.001);
    Test.assert((Geo.normaliserDegres(720.0)).abs() < 0.001);
    Test.assert(Geo.normaliserDegres(null) == null);
    return true;
}

(:test)
function testAngleRelatifSoustraitLeCapDeLaMontre(logger) {
    // Cible plein est (90), montre orientee plein nord (0) : la fleche
    // pointe a 90 degres, soit a droite.
    Test.assert((Geo.angleRelatif(90.0, 0.0) - 90.0).abs() < 0.001);
    // Montre deja orientee vers la cible : fleche tout droit.
    Test.assert(Geo.angleRelatif(90.0, 90.0).abs() < 0.001);
    // Montre orientee a l'oppose : fleche vers l'arriere.
    Test.assert((Geo.angleRelatif(90.0, 270.0) - 180.0).abs() < 0.001);
    return true;
}

(:test)
function testAngleRelatifResteDansZeroTroisCentSoixante(logger) {
    // Cap cible 10, montre a 350 : la difference brute vaut -340. Sans
    // normalisation, la fleche partirait a l'envers.
    var a = Geo.angleRelatif(10.0, 350.0);
    Test.assert((a - 20.0).abs() < 0.001);
    return true;
}

(:test)
function testAngleRelatifNulSiUnCapManque(logger) {
    // LA regle de cette page : sans cap de la montre, on connait la
    // direction du point dans le monde mais pas l'orientation du poignet.
    // Dessiner quand meme reviendrait a pointer le nord en pretendant
    // montrer la route.
    Test.assert(Geo.angleRelatif(90.0, null) == null);
    Test.assert(Geo.angleRelatif(null, 90.0) == null);
    Test.assert(Geo.angleRelatif(null, null) == null);
    return true;
}

(:test)
function testFormatDistanceMetresPuisKilometres(logger) {
    Test.assertEqual(Geo.formatDistance(0.0), "0 m");
    Test.assertEqual(Geo.formatDistance(820.0), "820 m");
    Test.assertEqual(Geo.formatDistance(999.0), "999 m");
    Test.assertEqual(Geo.formatDistance(1000.0), "1.0 km");
    Test.assertEqual(Geo.formatDistance(2400.0), "2.4 km");
    return true;
}

(:test)
function testFormatDistanceInconnueEstUnTiret(logger) {
    // Meme convention que partout ailleurs dans l'app : une valeur inconnue
    // est un tiret, jamais un zero -- "0 m" se lirait comme << vous y etes >>.
    Test.assertEqual(Geo.formatDistance(null), Fmt.DASH);
    return true;
}
