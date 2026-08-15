using Toybox.Test;
using Toybox.Application;

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
