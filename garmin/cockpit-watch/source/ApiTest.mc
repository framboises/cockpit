using Toybox.Test;
using Toybox.Lang;
using Toybox.Application;
using Toybox.Time;

// Support de capture pour les tests qui arment Api.mCallback directement,
// sans passer par le reseau. Api.mCallback n'est plus hidden precisement
// pour permettre ce genre de test (cf. commentaire dans Api.mc).
module ApiTestSupport {
    var mOk = null;
    var mSt = null;

    function capture(ok, st) {
        mOk = ok;
        mSt = st;
    }

    function reset() {
        mOk = null;
        mSt = null;
    }
}

(:test)
function testToCacheCompactsAlerts(logger) {
    var data = {"t" => 100, "n" => "24HM 26", "e" => 5, "er" => 6,
                "w" => 27.4, "wl" => 1,
                "al" => [{"l" => 3, "m" => "SOS"}, {"l" => 1, "m" => "x"}]};
    var st = Api.toCacheDict(data, 200);
    Test.assertEqual(st["al"].size(), 2);
    Test.assertEqual(st["al"][0][0], 3);
    Test.assertEqual(st["al"][0][1], "SOS");
    return true;
}

(:test)
function testToCacheStampsResponseTime(logger) {
    var st = Api.toCacheDict({"t" => 100}, 200);
    Test.assertEqual(st["rx"], 200);
    Test.assertEqual(st["t"], 100);
    return true;
}

// toCacheDict recopie champ par champ : un champ ajoute au payload serveur et
// oublie ici serait perdu en SILENCE. C'est exactement ce qui serait arrive a
// m/pk/pkt sans ces tests.

(:test)
function testToCacheReporteModeEtPic(logger) {
    var data = {"t" => 100, "n" => "LMC 26", "m" => "past",
                "e" => null, "er" => null,
                "pk" => 52409, "pkt" => 1783175368,
                "w" => 24.2, "wl" => 0, "al" => []};
    var st = Api.toCacheDict(data, 200);
    Test.assertEqual(st["m"], "past");
    Test.assertEqual(st["pk"], 52409);
    Test.assertEqual(st["pkt"], 1783175368);
    return true;
}

(:test)
function testToCacheSupposeLeDirectSansMode(logger) {
    // Un serveur anterieur a l'ajout de `m` ne l'envoie pas. Prendre son
    // silence pour du passe couperait le direct sur une montre a jour face a
    // un backend qui ne l'est pas encore.
    var st = Api.toCacheDict({"t" => 100, "e" => 5}, 200);
    Test.assertEqual(st["m"], "live");
    Test.assert(st["pk"] == null);
    Test.assert(st["pkt"] == null);
    return true;
}

(:test)
function testToCacheReporteLesQuatreBlocs(logger) {
    var data = {"t" => 100, "m" => "live", "mr" => null, "p" => 47320,
                "mc" => {"s" => [2, 14]}, "tr" => {"vd" => 2},
                "me" => {"tc" => 21.3}, "st" => {"pj" => 52100},
                "al" => []};
    var st = Api.toCacheDict(data, 200);
    Test.assertEqual(st["p"], 47320);
    var pg = Api.toPagesDict(data);
    Test.assertEqual(pg["tr"]["vd"], 2);
    Test.assertEqual(pg["st"]["pj"], 52100);
    return true;
}

(:test)
function testLeNoyauNePortePasLesBlocs(logger) {
    // Le cache est coupe en deux : la glance et le service de fond lisent le
    // noyau, sur un budget de 64 Ko, et n'ont aucune raison de deserialiser
    // du trafic qu'ils n'affichent jamais.
    var data = {"t" => 100, "tr" => {"vd" => 2}, "al" => []};
    var st = Api.toCacheDict(data, 200);
    Test.assert(!st.hasKey("tr"));
    return true;
}

(:test)
function testToEditionsCompacte(logger) {
    var data = {"ok" => true, "ed" => [
        {"n" => "LMC 26", "ev" => "LE MANS CLASSIC", "y" => 2026,
         "pk" => 52409, "pkt" => 1783175368},
        {"n" => "24HM 26", "ev" => "24H MOTOS", "y" => 2026,
         "pk" => 50690, "pkt" => 1776517509}]};
    var liste = Api.toEditionsList(data);
    Test.assertEqual(liste.size(), 2);
    Test.assertEqual(liste[0][0], "LMC 26");
    Test.assertEqual(liste[0][1], 52409);
    Test.assertEqual(liste[1][0], "24HM 26");
    return true;
}

(:test)
function testToEditionsSansListeNeCassePas(logger) {
    Test.assertEqual(Api.toEditionsList({"ok" => true}).size(), 0);
    Test.assertEqual(Api.toEditionsList(null).size(), 0);
    return true;
}

(:test)
function testToCacheHandlesMissingAlerts(logger) {
    var st = Api.toCacheDict({"t" => 100}, 200);
    Test.assertEqual(st["al"].size(), 0);
    return true;
}

// Les trois tests qui suivent couvrent onReceive() sans reseau : Api.mCallback
// est arme directement sur un support de capture local. Ce sont les cas les
// plus frequents en exploitation (telephone hors de portee, jeton revoque,
// rate limit, backend redemarre) et jusqu'ici aucun test ne les couvrait.

(:test)
function testOnReceiveHttp500NeCasseRien(logger) {
    ApiTestSupport.reset();
    Api.mCallback = new Lang.Method(ApiTestSupport, :capture);
    Api.mInFlightSince = 12345;
    Api.onReceive(500, null);
    Test.assertEqual(ApiTestSupport.mOk, false);
    Test.assert(ApiTestSupport.mSt == null);
    // Un echec ne doit pas laisser la garde armee : sinon le rafraichissement
    // suivant serait bloque pour rien pendant 30 s.
    Test.assert(Api.mInFlightSince == null);
    return true;
}

(:test)
function testOnReceive200SansCorpsEstUnEchec(logger) {
    ApiTestSupport.reset();
    Api.mCallback = new Lang.Method(ApiTestSupport, :capture);
    Api.mInFlightSince = 67890;
    Api.onReceive(200, null);
    Test.assertEqual(ApiTestSupport.mOk, false);
    Test.assert(ApiTestSupport.mSt == null);
    Test.assert(Api.mInFlightSince == null);
    return true;
}

(:test)
function testOnReceive200AvecPayloadEstUnSucces(logger) {
    ApiTestSupport.reset();
    Api.mCallback = new Lang.Method(ApiTestSupport, :capture);
    Api.mInFlightSince = 11111;
    var data = {"t" => 100, "n" => "24HM 26", "e" => 5, "er" => 6,
                "w" => 27.4, "wl" => 1, "al" => []};
    Api.onReceive(200, data);
    Test.assertEqual(ApiTestSupport.mOk, true);
    Test.assert(ApiTestSupport.mSt != null);
    Test.assertEqual(ApiTestSupport.mSt["e"], 5);
    Test.assertEqual(ApiTestSupport.mSt["al"].size(), 0);
    Test.assert(Api.mInFlightSince == null);
    return true;
}

(:test)
function testOnReceiveSauvegardeAussiLesQuatreBlocs(logger) {
    // Le wiring reel : onReceive doit pousser les blocs dans la seconde cle
    // du cache, pas seulement les rendre disponibles via toPagesDict.
    ApiTestSupport.reset();
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    Api.mCallback = new Lang.Method(ApiTestSupport, :capture);
    Api.mInFlightSince = 22222;
    var data = {"t" => 100, "e" => 5, "al" => [],
                "mc" => {"s" => [2, 14]}, "tr" => {"vd" => 2},
                "me" => null, "st" => {"pj" => 52100}};
    Api.onReceive(200, data);
    var pg = Cache.loadPages();
    Test.assert(pg != null);
    Test.assertEqual(pg["tr"]["vd"], 2);
    Test.assertEqual(pg["st"]["pj"], 52100);
    Test.assert(pg["me"] == null);
    return true;
}

// Verifie que la garde anti-doublon mord reellement : une requete deja en
// vol (posee il y a 1 s, bien avant le timeout de 30 s) doit court-circuiter
// fetch() et rappeler (false, null) sans jamais toucher au reseau.
(:test)
function testFetchRefuseUneRequeteDejaEnVol(logger) {
    ApiTestSupport.reset();
    var hostAvant = Application.Properties.getValue("host");
    var tokenAvant = Application.Properties.getValue("token");
    var mockAvant = Application.Properties.getValue("mockData");

    Application.Properties.setValue("host", "cockpit.lemans.org");
    Application.Properties.setValue("token", "test-token-verif-tache9");
    Application.Properties.setValue("mockData", false);

    Api.mCallback = null;
    Api.mInFlightSince = Time.now().value() - 1;
    Api.fetch(new Lang.Method(ApiTestSupport, :capture));

    Test.assertEqual(ApiTestSupport.mOk, false);
    Test.assert(ApiTestSupport.mSt == null);

    // Restaure les proprietes et l'etat interne pour ne rien faire fuiter
    // vers les autres tests.
    Application.Properties.setValue("host", hostAvant);
    Application.Properties.setValue("token", tokenAvant);
    Application.Properties.setValue("mockData", mockAvant);
    Api.mInFlightSince = null;
    return true;
}

// Fix round 1, tache 9 : la branche mock de fetch() invoquait directement le
// callback avec Mock.state() -- Cache.save() dans onFetched stockait alors
// les quatre blocs mc/tr/me/st verbatim dans le NOYAU, exactement la forme
// que la coupure en deux cles Storage cherche a empecher (et que la vraie
// glance ne recoit jamais). fetch() en mode mock doit etre symetrique du
// chemin reseau (onReceive) : toCacheDict pour le noyau, toPagesDict +
// Cache.savePages pour les blocs.
(:test)
function testFetchModeMockRepartitLesQuatreBlocsCommeLeReseau(logger) {
    ApiTestSupport.reset();
    Application.Storage.deleteValue(Cache.KEY_PAGES);

    var mockAvant = Application.Properties.getValue("mockData");
    var scenarioAvant = Application.Properties.getValue("mockScenario");
    Application.Properties.setValue("mockData", true);
    Application.Properties.setValue("mockScenario", 2);

    Api.mCallback = null;
    Api.fetch(new Lang.Method(ApiTestSupport, :capture));

    Test.assertEqual(ApiTestSupport.mOk, true);
    var st = ApiTestSupport.mSt;
    Test.assert(st != null);
    // Le noyau ne porte JAMAIS les quatre blocs.
    Test.assert(!st.hasKey("mc"));
    Test.assert(!st.hasKey("tr"));
    Test.assert(!st.hasKey("me"));
    Test.assert(!st.hasKey("st"));
    // Mais garde bien les champs qui lui reviennent, passes par
    // toCacheDict -- y compris les alertes, converties depuis le format brut
    // {"l"=>, "m"=>} vers les tuples compacts du cache.
    Test.assertEqual(st["p"], 58400);
    Test.assertEqual(st["al"].size(), 3);
    Test.assertEqual(st["al"][0][0], 3);
    Test.assertEqual(st["al"][0][1], "SOS tablette");

    // Les quatre blocs, eux, atterrissent dans la seconde cle.
    var pg = Cache.loadPages();
    Test.assert(pg != null);
    Test.assertEqual(pg["tr"]["vd"], 3);
    Test.assertEqual(pg["mc"]["s"][0], 3);

    Application.Properties.setValue("mockData", mockAvant);
    Application.Properties.setValue("mockScenario", scenarioAvant);
    return true;
}


// --- Transport du guidage ----------------------------------------------
//
// Les trois tests qui suivent ont ete ecrits APRES un sabotage qui n'avait
// rien fait tomber : supprimer `gd` de toPagesDict, ou `gs` de toCacheDict,
// laissait 194 tests au vert alors que la page Guidage serait restee
// definitivement vide et la vibration definitivement muette. C'est
// exactement le defaut que le commentaire de toCacheDict annonce -- il
// s'etait deja produit sur `me` -- et qu'aucun test ne couvrait encore
// pour ces deux champs-la.

(:test)
function testToPagesReporteLePointDeGuidage(logger) {
    var data = {"t" => 100, "mc" => null, "tr" => null, "me" => null,
                "st" => null,
                "gd" => {"lat" => 47.9503, "lon" => 0.2214,
                         "n" => "Porte Houx 5", "s" => 3}};
    var pg = Api.toPagesDict(data);
    Test.assert(pg["gd"] != null);
    Test.assertEqual(pg["gd"]["n"], "Porte Houx 5");
    Test.assertEqual(pg["gd"]["s"], 3);
    return true;
}

(:test)
function testToCacheReporteLaSequenceDeGuidage(logger) {
    // `gs` est le SEUL champ de guidage dans le noyau, et c'est lui qui
    // declenche la vibration : le service de fond ne lit que le noyau.
    var data = {"t" => 100, "m" => "live", "al" => [], "gs" => 7,
                "gd" => {"lat" => 47.9503, "lon" => 0.2214, "n" => "X",
                         "s" => 7}};
    var st = Api.toCacheDict(data, 200);
    Test.assertEqual(st["gs"], 7);
    // Le point COMPLET, lui, ne doit pas encombrer le noyau : la glance le
    // deserialiserait a chaque affichage sans jamais l'utiliser.
    Test.assert(!st.hasKey("gd"));
    return true;
}

(:test)
function testSansGuidageLesDeuxChampsRestentNuls(logger) {
    // Payload d'un serveur anterieur a cette fonctionnalite : ni l'un ni
    // l'autre ne doit lever, et surtout aucun ne doit prendre une valeur
    // par defaut qui ferait vibrer la montre.
    var data = {"t" => 100, "m" => "live", "al" => []};
    var st = Api.toCacheDict(data, 200);
    var pg = Api.toPagesDict(data);
    Test.assert(st["gs"] == null);
    Test.assert(pg["gd"] == null);
    return true;
}
