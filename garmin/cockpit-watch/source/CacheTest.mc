using Toybox.Test;
using Toybox.Application;

(:test)
function testCacheRoundTrip(logger) {
    Cache.clear();
    Cache.save({"v" => 1, "t" => 100, "e" => 42});
    var st = Cache.load();
    Test.assert(st != null);
    Test.assertEqual(st["e"], 42);
    return true;
}

(:test)
function testCacheRejectsOtherSchema(logger) {
    Application.Storage.setValue(Cache.KEY, {"v" => 99, "e" => 1});
    // Test.assertEqual invoque value1.equals(value2) : impossible quand la
    // valeur calculee est elle-meme null (Null n'implemente pas equals()).
    Test.assert(Cache.load() == null);
    return true;
}

(:test)
function testCacheEmptyIsNull(logger) {
    Cache.clear();
    Test.assert(Cache.load() == null);
    return true;
}

(:test)
function testAlertMaxTakesHighest(logger) {
    var st = {"al" => [[1, "a"], [3, "b"], [2, "c"]]};
    Test.assertEqual(State.alertMax(st), 3);
    return true;
}

(:test)
function testAlertMaxEmptyIsZero(logger) {
    Test.assertEqual(State.alertMax({"al" => []}), 0);
    Test.assertEqual(State.alertMax({}), 0);
    Test.assertEqual(State.alertMax(null), 0);
    return true;
}

(:test)
function testWorstLevelTakesHigher(logger) {
    Test.assertEqual(State.worstLevel({"wl" => 1, "al" => [[3, "x"]]}), 3);
    Test.assertEqual(State.worstLevel({"wl" => 3, "al" => [[1, "x"]]}), 3);
    Test.assertEqual(State.worstLevel({"wl" => 2, "al" => []}), 2);
    Test.assertEqual(State.worstLevel({}), 0);
    Test.assertEqual(State.worstLevel(null), 0);
    return true;
}

(:test)
function testWorstAgeTakesOlder(logger) {
    // donnee de 300 s, reponse de 10 s : c'est la donnee qui est perimee
    var st = {"t" => 700, "rx" => 990};
    Test.assertEqual(State.worstAgeSec(st, 1000), 300);
    return true;
}

(:test)
function testWorstAgeHandlesMissing(logger) {
    Test.assertEqual(State.worstAgeSec({"rx" => 990}, 1000), 10);
    Test.assert(State.worstAgeSec({}, 1000) == null);
    return true;
}

(:test)
function testIsStale(logger) {
    Test.assertEqual(State.isStale({"t" => 900, "rx" => 900}, 1000, 90), true);
    Test.assertEqual(State.isStale({"t" => 950, "rx" => 950}, 1000, 90), false);
    // sans donnee du tout, on considere perime
    Test.assertEqual(State.isStale({}, 1000, 90), true);
    return true;
}
