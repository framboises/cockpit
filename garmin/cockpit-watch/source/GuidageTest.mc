using Toybox.Test;
using Toybox.Application;
using Toybox.Time;
using Toybox.Position;

// Les tests de la page Guidage portent sur des VALEURS, pas sur l'absence
// d'exception : une fleche dessinee par defaut vers le haut ne leve rien et
// pointe pourtant n'importe ou. C'est exactement le defaut que cette page
// existe pour ne pas commettre.

function guidagePages(gd) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    Cache.savePages({"mc" => null, "tr" => null, "me" => null, "st" => null,
                     "gd" => gd});
}

// Porte Houx 5 (coin nord-ouest du circuit) et un point de reference a 1 km
// plein sud, pour que les attentes se verifient a la main.
const GD_HOUX = {"lat" => 47.9503, "lon" => 0.2214, "n" => "Porte Houx 5",
                 "s" => 3, "t" => 1786000000};

(:test)
function testSansPointLEtatEstSansPoint(logger) {
    guidagePages(null);
    var vue = new GuidageView();
    Test.assertEqual(vue.etat(), "sans_point");
    Test.assert(vue.point() == null);
    return true;
}

(:test)
function testPointLuDepuisLesPages(logger) {
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    Test.assertEqual(vue.point()["n"], "Porte Houx 5");
    return true;
}

(:test)
function testSansPositionLEtatEstRechercheGps(logger) {
    // Un point connu mais pas encore de fix : la page doit dire qu'elle
    // cherche, pas afficher une distance inventee.
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    Test.assertEqual(vue.etat(), "recherche_gps");
    Test.assert(vue.distanceM() == null);
    Test.assert(vue.angleFleche() == null);
    return true;
}

(:test)
function testDistanceCalculeeDesQueLaPositionArrive(logger) {
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    // 0,001 degre de latitude au sud du point = ~111 m.
    vue.injecterPourTest([47.9493, 0.2214], null, null);
    var d = vue.distanceM();
    Test.assert(d != null);
    Test.assert(d > 105.0);
    Test.assert(d < 118.0);
    return true;
}

(:test)
function testSansCapAucuneFlecheMaisUneDistance(logger) {
    // LA regle de cette page. Sans cap de la montre, on connait la direction
    // du point dans le monde mais pas l'orientation du poignet : dessiner
    // reviendrait a pointer le nord en pretendant montrer la route.
    // La distance, elle, reste juste et doit s'afficher.
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    vue.injecterPourTest([47.9493, 0.2214], null, null);
    Test.assertEqual(vue.etat(), "sans_cap");
    Test.assert(vue.angleFleche() == null);
    Test.assert(vue.distanceM() != null);
    return true;
}

(:test)
function testFlecheDroitDevantQuandLaMontreVisLeaPoint(logger) {
    // Point plein NORD de la position, montre orientee plein nord : la
    // fleche doit pointer tout droit (0 degre).
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    vue.injecterPourTest([47.9493, 0.2214], 0.0, null);
    Test.assertEqual(vue.etat(), "guide");
    var a = vue.angleFleche();
    Test.assert(a != null);
    Test.assert(a < 1.0 || a > 359.0);
    return true;
}

(:test)
function testFlecheADroiteQuandLaMontreEstTourneeAGauche(logger) {
    // Meme geometrie, mais la montre regarde plein OUEST (270) : le point
    // est alors a sa droite, donc la fleche a 90 degres.
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    vue.injecterPourTest([47.9493, 0.2214], 270.0, null);
    var a = vue.angleFleche();
    Test.assert((a - 90.0).abs() < 1.0);
    return true;
}

(:test)
function testFlecheVersLArriereQuandOnTourneLeDos(logger) {
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    vue.injecterPourTest([47.9493, 0.2214], 180.0, null);
    var a = vue.angleFleche();
    Test.assert((a - 180.0).abs() < 1.0);
    return true;
}

(:test)
function testAucuneFlecheSansPointMemeAvecGpsEtCap(logger) {
    // Symetrique : tout le materiel repond, mais personne n'a envoye de
    // point. Rien a montrer.
    guidagePages(null);
    var vue = new GuidageView();
    vue.injecterPourTest([47.9493, 0.2214], 0.0, null);
    Test.assert(vue.angleFleche() == null);
    Test.assert(vue.distanceM() == null);
    Test.assertEqual(vue.etat(), "sans_point");
    return true;
}

(:test)
function testMotDePrecisionNommeChaqueQualite(logger) {
    // C'est le MOT, pas la couleur, qui dit a l'utilisateur s'il peut suivre
    // la fleche : un daltonien en plein soleil ne distingue pas le vert de
    // l'ambre.
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    vue.injecterPourTest(null, null, null);
    Test.assertEqual(vue.motPrecision(), "recherche GPS");
    vue.injecterPourTest(null, null, Position.QUALITY_GOOD);
    Test.assertEqual(vue.motPrecision(), "GPS bon");
    vue.injecterPourTest(null, null, Position.QUALITY_USABLE);
    Test.assertEqual(vue.motPrecision(), "GPS moyen");
    vue.injecterPourTest(null, null, Position.QUALITY_POOR);
    Test.assertEqual(vue.motPrecision(), "GPS faible");
    return true;
}

(:test)
function testDesactiverOublieLesMesures(logger) {
    // Garder la derniere position ferait afficher, au retour sur la page,
    // une fleche calculee sur un fix vieux de plusieurs minutes -- juste
    // assez plausible pour qu'on la suive.
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    vue.activer();
    vue.injecterPourTest([47.9493, 0.2214], 0.0, Position.QUALITY_GOOD);
    Test.assert(vue.angleFleche() != null);
    vue.desactiver();
    Test.assert(vue.angleFleche() == null);
    Test.assert(vue.distanceM() == null);
    Test.assertEqual(vue.etat(), "recherche_gps");
    return true;
}

(:test)
function testActiverEstIdempotent(logger) {
    // setPage peut etre appele deux fois de suite sur la meme page (menu de
    // saut, puis HAUT/BAS) : rallumer un GPS deja allume ne doit pas
    // empiler deux abonnements.
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    vue.activer();
    Test.assertEqual(vue.estActive(), true);
    vue.activer();
    Test.assertEqual(vue.estActive(), true);
    vue.desactiver();
    Test.assertEqual(vue.estActive(), false);
    vue.desactiver();
    Test.assertEqual(vue.estActive(), false);
    return true;
}

(:test)
function testEnregistrerSansPointNeFaitRien(logger) {
    guidagePages(null);
    var vue = new GuidageView();
    Test.assertEqual(vue.enregistrerWaypoint(), false);
    return true;
}
