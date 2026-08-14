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
