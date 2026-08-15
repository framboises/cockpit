using Toybox.Test;
using Toybox.Application;
using Toybox.Math;

(:test)
function testVerdictMotSuitLesMemsMotsQueLeMur(logger) {
    // circulation.html:494 -- le poignet et l'ecran doivent dire la meme
    // chose du meme etat.
    Test.assertEqual(Pages.verdictMot(3), "CRITIQUE");
    Test.assertEqual(Pages.verdictMot(5), "CRITIQUE");
    Test.assertEqual(Pages.verdictMot(2), "TENSION");
    Test.assertEqual(Pages.verdictMot(1), "VIGILANCE");
    Test.assertEqual(Pages.verdictMot(0), "FLUIDE");
    Test.assertEqual(Pages.verdictMot(null), "--");
    return true;
}

(:test)
function testAccesseursRenvoientLeBlocNomme(logger) {
    var pg = {"mc" => {"a" => 1}, "tr" => {"vd" => 2},
              "me" => {"tc" => 21.3}, "st" => {"pj" => 52100}};
    Test.assertEqual(Pages.mainCourante(pg)["a"], 1);
    Test.assertEqual(Pages.trafic(pg)["vd"], 2);
    Test.assertEqual(Pages.meteo(pg)["tc"], 21.3);
    Test.assertEqual(Pages.frequentation(pg)["pj"], 52100);
    return true;
}

(:test)
function testAccesseursGerentUnBlocNull(logger) {
    // Le serveur met a null le bloc dont la source est tombee : les
    // accesseurs ne doivent pas planter dessus.
    var pg = {"mc" => null, "tr" => null, "me" => null, "st" => null};
    Test.assert(Pages.mainCourante(pg) == null);
    Test.assert(Pages.trafic(pg) == null);
    Test.assert(Pages.meteo(pg) == null);
    Test.assert(Pages.frequentation(pg) == null);
    return true;
}

(:test)
function testAccesseursGerentPgNull(logger) {
    // Aucun cache pages disponible (jamais fetch, ou schema rejete).
    Test.assert(Pages.mainCourante(null) == null);
    Test.assert(Pages.trafic(null) == null);
    Test.assert(Pages.meteo(null) == null);
    Test.assert(Pages.frequentation(null) == null);
    return true;
}

(:test)
function testCachePagesRoundTrip(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    Cache.savePages({"mc" => {"a" => 1}, "tr" => {"vd" => 2},
                      "me" => null, "st" => null});
    var pg = Cache.loadPages();
    Test.assert(pg != null);
    Test.assertEqual(pg["tr"]["vd"], 2);
    Test.assert(pg["me"] == null);
    return true;
}

(:test)
function testCachePagesRejectsOtherSchema(logger) {
    Application.Storage.setValue(Cache.KEY_PAGES, {"v" => 99, "tr" => {"vd" => 1}});
    Test.assert(Cache.loadPages() == null);
    return true;
}

(:test)
function testCachePagesEmptyIsNull(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    Test.assert(Cache.loadPages() == null);
    return true;
}

(:test)
function testCachePagesNAffecteJamaisLeNoyau(logger) {
    // Le cache est coupe en deux : ecrire les pages ne doit rien changer au
    // noyau (cle distincte), et reciproquement.
    Cache.clear();
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    Cache.save({"v" => 1, "t" => 100, "e" => 42});
    Cache.savePages({"mc" => null, "tr" => {"vd" => 3}, "me" => null, "st" => null});
    var st = Cache.load();
    var pg = Cache.loadPages();
    Test.assertEqual(st["e"], 42);
    Test.assertEqual(pg["tr"]["vd"], 3);
    Test.assert(!st.hasKey("tr"));
    return true;
}

// Vigilance Meteo-France : echelle 0-3 distincte du verdict trafic et de la
// consigne (meteo_etat.ORDRE_COULEURS). null pour "vert" ET "inconnu" -- les
// deux etats ou rien ne doit s'afficher, cf. la regle commune "absente quand
// tout est calme".

(:test)
function testVigilanceMotNommeLesTroisNiveaux(logger) {
    Test.assertEqual(Pages.vigilanceMot(1), "VIGILANCE JAUNE");
    Test.assertEqual(Pages.vigilanceMot(2), "VIGILANCE ORANGE");
    Test.assertEqual(Pages.vigilanceMot(3), "VIGILANCE ROUGE");
    Test.assertEqual(Pages.vigilanceMot(5), "VIGILANCE ROUGE");
    return true;
}

(:test)
function testVigilanceMotNullQuandVertOuInconnu(logger) {
    Test.assert(Pages.vigilanceMot(0) == null);
    Test.assert(Pages.vigilanceMot(null) == null);
    return true;
}

(:test)
function testVigilanceMotCourtPartageLaMemeEchelle(logger) {
    Test.assertEqual(Pages.vigilanceMotCourt(1), "VIG JAUNE");
    Test.assertEqual(Pages.vigilanceMotCourt(2), "VIG ORANGE");
    Test.assertEqual(Pages.vigilanceMotCourt(3), "VIG ROUGE");
    Test.assert(Pages.vigilanceMotCourt(0) == null);
    Test.assert(Pages.vigilanceMotCourt(null) == null);
    return true;
}

// largeurUtile(dc, y, hauteur) : le bord CONTRAIGNANT est celui le plus
// eloigne du centre (140 sur un ecran de 280), pas l'ancre (y) seule. Ces
// tests figent en VALEUR les quatre ecarts mesures a la relecture -- avant
// correction, chaque appelant passait juste `y` et surestimait la place
// disponible.

(:test)
function testLargeurUtileBlocEntierementEnHautUtiliseLeSommet(logger) {
    // Bloc [24,46], entierement en moitie haute : le sommet (24, le plus
    // eloigne du centre) contraint deja, comme avant la correction -- ce
    // cas ne doit PAS changer de resultat.
    var dc = dcDeTest();
    var attendu = 2.0 * Math.sqrt(140.0 * 140.0 - 116.0 * 116.0);
    Test.assertEqual(Pages.largeurUtile(dc, 24, 22).toNumber(), attendu.toNumber());
    return true;
}

(:test)
function testLargeurUtileBlocEnBasUtiliseLaBase(logger) {
    // Bloc [217,239] (ligne "edition", FrequentationView) : la BASE (239),
    // pas le sommet (217), est la plus eloignee du centre. L'ancien calcul
    // (largeurUtile(dc, 217)) annoncait 233,85 px ; le bon bord donne
    // 198,0 px -- l'ecart (35,9 px) mesure a la relecture.
    var dc = dcDeTest();
    var ancre = Pages.largeurUtile(dc, 217, 0);
    var correct = Pages.largeurUtile(dc, 217, 22);
    Test.assert(correct < ancre);
    Test.assert((correct - 198.0).abs() < 0.5);
    Test.assert((ancre - 233.85).abs() < 0.5);
    return true;
}

(:test)
function testLargeurUtileConsigneMeteoDeuxLignes(logger) {
    // Les deux dispo de MeteoView (consigne, FONT_XTINY, hauteur 22) :
    // ligne 1 a y=161 (base 183), ligne 2 a y=181 (base 203).
    var dc = dcDeTest();
    var dispo1 = Pages.largeurUtile(dc, 161, 22);
    var dispo2 = Pages.largeurUtile(dc, 181, 22);
    Test.assert((dispo1 - 266.46).abs() < 0.5);
    Test.assert((dispo2 - 250.05).abs() < 0.5);
    return true;
}

(:test)
function testLargeurUtileTraficDeuxiemeTerrain(logger) {
    // TraficView, nom du 2e terrain (FONT_SMALL, hauteur 34) a y=153 :
    // la base (187) contraint, pas le sommet (153).
    var dc = dcDeTest();
    var dispo = Pages.largeurUtile(dc, 153, 34);
    Test.assert((dispo - 263.7).abs() < 0.5);
    return true;
}

(:test)
function testLargeurUtileZeroHorsDuCadran(logger) {
    // Bloc entierement hors du cercle (y=280, hauteur=0) : corde nulle,
    // jamais negative ni une exception de racine de nombre negatif.
    var dc = dcDeTest();
    Test.assertEqual(Pages.largeurUtile(dc, 280, 0), 0.0);
    Test.assertEqual(Pages.largeurUtile(dc, -50, 0), 0.0);
    return true;
}
