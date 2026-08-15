using Toybox.Test;

// Le brief annoncait un StateTest existant : il n'y en avait pas. Les gardes
// de peremption n'etaient couvertes par aucun test, alors que ce sont elles
// qui decident si la montre crie "perime".

(:test)
function testIsPastReconnaitLeMode(logger) {
    Test.assertEqual(State.isPast({"m" => "past"}), true);
    Test.assertEqual(State.isPast({"m" => "live"}), false);
    return true;
}

(:test)
function testIsPastToleereLAbsenceDeMode(logger) {
    // Un cache ecrit par une version anterieure du transport n'a pas de `m`.
    // Le prendre pour du passe couperait le direct au premier demarrage
    // apres mise a jour.
    Test.assertEqual(State.isPast({"t" => 100}), false);
    Test.assertEqual(State.isPast(null), false);
    return true;
}

(:test)
function testEditionCloseNEstJamaisPerimee(logger) {
    // LA regression a tenir. En mode past, `t` est nul par construction : le
    // pic d'une edition close est definitif, rien n'y vieillit. Sans le
    // court-circuit, la montre afficherait "perime" en permanence hors
    // evenement, c'est-a-dire l'essentiel de l'annee.
    var st = {"m" => "past", "t" => null, "rx" => 1000,
              "pk" => 52409, "pkt" => 900, "al" => []};
    // Reponse recue il y a une journee entiere : toujours pas perimee.
    Test.assertEqual(State.isStale(st, 1000 + 86400, 90), false);
    return true;
}

(:test)
function testDirectPerimeResteDetecte(logger) {
    // Le court-circuit ne doit pas desarmer la detection en direct : c'est
    // la panne que la montre doit signaler (telephone hors de portee, jeton
    // revoque, certificat expire).
    var st = {"m" => "live", "t" => 1000, "rx" => 1000, "al" => []};
    Test.assertEqual(State.isStale(st, 1000 + 30, 90), false);
    Test.assertEqual(State.isStale(st, 1000 + 200, 90), true);
    return true;
}

(:test)
function testAucuneDonneeEstPerimee(logger) {
    Test.assertEqual(State.isStale(null, 1000, 90), true);
    Test.assertEqual(State.isStale({"al" => []}, 1000, 90), true);
    return true;
}

(:test)
function testWorstLevelInchangeEnModePast(logger) {
    // worstLevel ne connait pas le mode : une chaleur dangereuse reste
    // dangereuse hors evenement, la meteo ne s'arrete pas avec la course.
    var st = {"m" => "past", "wl" => 2, "al" => [[1, "x"]]};
    Test.assertEqual(State.worstLevel(st), 2);
    return true;
}
