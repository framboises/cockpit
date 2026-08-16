using Toybox.Test;
using Toybox.Application;

(:test)
function testAlertsOnRise(logger) {
    Test.assertEqual(Alerting.shouldAlert(0, 1), true);
    Test.assertEqual(Alerting.shouldAlert(1, 3), true);
    return true;
}

(:test)
function testSilentOnFallOrPlateau(logger) {
    Test.assertEqual(Alerting.shouldAlert(2, 2), false);
    Test.assertEqual(Alerting.shouldAlert(3, 1), false);
    return true;
}

(:test)
function testFirstReadingDoesNotAlert(logger) {
    // Sans reference anterieure, on ne vibre pas : sinon la premiere synchro
    // apres installation reveille le porteur pour rien.
    Test.assertEqual(Alerting.shouldAlert(null, 2), false);
    return true;
}

(:test)
function testCheckMemorisesLevels(logger) {
    Alerting.reset();
    Alerting.check({"wl" => 1, "al" => []});
    Test.assertEqual(Application.Storage.getValue(Alerting.KEY_WL), 1);
    Test.assertEqual(Application.Storage.getValue(Alerting.KEY_AL), 0);
    return true;
}

(:test)
function testCheckTriggersOnceOnly(logger) {
    Alerting.reset();
    Alerting.check({"wl" => 0, "al" => []});
    var premier = Alerting.check({"wl" => 2, "al" => []});
    var second = Alerting.check({"wl" => 2, "al" => []});
    Test.assertEqual(premier, true);
    Test.assertEqual(second, false);
    return true;
}

(:test)
function testAlertLevelAlsoTriggers(logger) {
    Alerting.reset();
    Alerting.check({"wl" => 0, "al" => []});
    var declenche = Alerting.check({"wl" => 0, "al" => [[3, "SOS"]]});
    Test.assertEqual(declenche, true);
    return true;
}


// --- Vibration a l'arrivee d'un point de guidage ------------------------
//
// Le guidage se distingue des seuils WBGT et des alertes : il vibre sur tout
// CHANGEMENT de sequence, pas seulement a la hausse. Un guidage n'a pas de
// gravite -- il n'y a pas de sequence << moins grave >> qu'une autre.

(:test)
function testGuidageVibreSurNouvelEnvoi(logger) {
    Test.assertEqual(Alerting.guidageChange(3, 4), true);
    return true;
}

(:test)
function testGuidageVibreAussiSiLaSequenceRedescend(logger) {
    // LE cas que la regle << a la hausse >> raterait : effacer un guidage
    // puis en renvoyer un fait repartir la sequence a 1 cote serveur (le
    // document est supprime, pas vide). Un nouveau point serait alors
    // silencieux, et personne ne saurait qu'un ordre est arrive.
    Test.assertEqual(Alerting.guidageChange(7, 1), true);
    return true;
}

(:test)
function testGuidageMuetSurSequenceInchangee(logger) {
    // Le cas ECRASANTEMENT majoritaire : la montre interroge le serveur
    // toutes les minutes, et le point ne change pas entre deux releves.
    Test.assertEqual(Alerting.guidageChange(4, 4), false);
    return true;
}

(:test)
function testGuidageMuetSansReferenceAnterieure(logger) {
    // Premier releve apres installation ou apres vidage du cache : on ne
    // sait pas si ce point est nouveau. Vibrer ferait sonner la montre au
    // demarrage pour un ordre peut-etre vieux d'une heure -- meme prudence
    // que shouldAlert sur un niveau inconnu.
    Test.assertEqual(Alerting.guidageChange(null, 5), false);
    return true;
}

(:test)
function testEffacementDUnGuidageNeVibrePas(logger) {
    // Passer d'un point a AUCUN point est une ANNULATION, pas une consigne.
    // Faire vibrer la montre parce qu'on a retire un ordre serait absurde.
    Test.assertEqual(Alerting.guidageChange(5, null), false);
    Test.assertEqual(Alerting.guidageChange(null, null), false);
    return true;
}

(:test)
function testCheckVibreALArriveeDUnPoint(logger) {
    // Bout en bout du chemin reel : deux appels a check(), le second portant
    // une sequence differente. C'est ce chemin-la, et pas guidageChange
    // seule, que le service de fond emprunte.
    Alerting.reset();
    Test.assertEqual(Alerting.check({"wl" => 0, "al" => [], "gs" => 4}), false);
    Test.assertEqual(Alerting.check({"wl" => 0, "al" => [], "gs" => 5}), true);
    // Troisieme releve identique : silence.
    Test.assertEqual(Alerting.check({"wl" => 0, "al" => [], "gs" => 5}), false);
    Alerting.reset();
    return true;
}

(:test)
function testCheckSansGuidageSeComporteCommeAvant(logger) {
    // Non-regression : un payload sans champ `gs` (cache ecrit par une
    // version anterieure) ne doit ni lever ni vibrer.
    Alerting.reset();
    Test.assertEqual(Alerting.check({"wl" => 0, "al" => []}), false);
    Test.assertEqual(Alerting.check({"wl" => 0, "al" => []}), false);
    Alerting.reset();
    return true;
}
