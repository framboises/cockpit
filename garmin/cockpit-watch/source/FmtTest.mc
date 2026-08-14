using Toybox.Test;

(:test)
function testCountGroupsThousands(logger) {
    Test.assertEqual(Fmt.count(48213), "48 213");
    Test.assertEqual(Fmt.count(213), "213");
    Test.assertEqual(Fmt.count(1000), "1 000");
    Test.assertEqual(Fmt.count(1234567), "1 234 567");
    Test.assertEqual(Fmt.count(0), "0");
    return true;
}

(:test)
function testCountNullIsDashes(logger) {
    Test.assertEqual(Fmt.count(null), "--");
    return true;
}

(:test)
function testAgeUnderOneMinute(logger) {
    Test.assertEqual(Fmt.age(30), "30 s");
    return true;
}

(:test)
function testAgeInMinutes(logger) {
    Test.assertEqual(Fmt.age(95), "1 min");
    Test.assertEqual(Fmt.age(600), "10 min");
    return true;
}

(:test)
function testAgeInHours(logger) {
    Test.assertEqual(Fmt.age(3700), "1 h");
    return true;
}

(:test)
function testAgeNullIsDashes(logger) {
    Test.assertEqual(Fmt.age(null), "--");
    return true;
}

(:test)
function testWbgtOneDecimal(logger) {
    Test.assertEqual(Fmt.wbgt(27.4), "27.4");
    Test.assertEqual(Fmt.wbgt(null), "--");
    return true;
}
