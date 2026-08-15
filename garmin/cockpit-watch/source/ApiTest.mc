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
