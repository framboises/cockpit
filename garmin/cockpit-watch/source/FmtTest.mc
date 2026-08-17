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
function testAgeEnJoursAuDelaDeDeuxJours(logger) {
    // 2725 h illisibles sur la montre : au-dela de deux jours on passe aux jours.
    Test.assertEqual(Fmt.age(172799), "47 h");
    Test.assertEqual(Fmt.age(172800), "2 j");
    Test.assertEqual(Fmt.age(9810000), "113 j");
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

// Le pic reutilise `count` : meme separateur de milliers que le compteur, donc
// deux nombres de meme nature se lisent pareil.
(:test)
function testPicUtiliseLeMemeGroupement(logger) {
    Test.assertEqual(Fmt.count(52409), "52 409");
    Test.assertEqual(Fmt.count(148919), "148 919");
    return true;
}

(:test)
function testJourEtHeureNullsSontDesTirets(logger) {
    Test.assertEqual(Fmt.day(null), "--");
    Test.assertEqual(Fmt.hour(null), "--");
    return true;
}

// Ces deux tests n'affirment PAS une valeur absolue : le rendu depend du
// fuseau de la montre, et l'epingler ferait passer le test sur un poste et
// echouer sur un autre sans que rien ne soit casse. On verifie la forme et
// une invariance vraie sous tout fuseau.

(:test)
function testHeureALaBonneForme(logger) {
    var rendu = Fmt.hour(1776517509);
    Test.assertEqual(rendu.length(), 5);
    Test.assertEqual(rendu.substring(2, 3), "h");
    return true;
}

(:test)
function testUneHeureDePlusDecaleDUneHeure(logger) {
    // Invariant valable sous tout fuseau : deux instants a 3600 s d'ecart ne
    // peuvent pas rendre la meme heure. C'est ce qui attraperait un epoch
    // fige ou une conversion qui ignore son argument.
    var a = Fmt.hour(1776517509);
    var b = Fmt.hour(1776517509 + 3600);
    Test.assert(!a.equals(b));
    return true;
}

(:test)
function testJourChangeDUnJourALAutre(logger) {
    var a = Fmt.day(1776517509);
    var b = Fmt.day(1776517509 + 86400);
    Test.assert(!a.equals(b));
    Test.assert(!a.equals("--"));
    return true;
}

// --- Compte a rebours de la page Timeline -------------------------------
//
// C'est la valeur de la page : une heure seule ("08:00") oblige a un calcul
// mental, un delai se lit d'un coup. Teste en VALEUR -- une formule fausse
// ne leve rien, elle affiche juste un mauvais chiffre.

(:test)
function testDelaiEnMinutes(logger) {
    Test.assertEqual(Fmt.delai(1000 + 2520, 1000), "dans 42 min");
    Test.assertEqual(Fmt.delai(1000 + 60, 1000), "dans 1 min");
    return true;
}

(:test)
function testDelaiEnHeuresAuDelaDUneHeure(logger) {
    Test.assertEqual(Fmt.delai(1000 + 3600, 1000), "dans 1 h");
    Test.assertEqual(Fmt.delai(1000 + 11520, 1000), "dans 3 h");
    return true;
}

(:test)
function testSousUneMinuteDitMaintenant(logger) {
    // "dans 0 min" se lirait comme une erreur d'affichage.
    Test.assertEqual(Fmt.delai(1000 + 59, 1000), "maintenant");
    Test.assertEqual(Fmt.delai(1000, 1000), "maintenant");
    return true;
}

(:test)
function testInstantPasseDitMaintenantPasUnNegatif(logger) {
    // Entre le dernier releve et l'affichage, une vignette peut avoir
    // franchi son heure sans que le serveur ait eu le temps de la retirer.
    // "dans -2 min" serait absurde.
    Test.assertEqual(Fmt.delai(1000 - 120, 1000), "maintenant");
    return true;
}

(:test)
function testDelaiInconnuEstUnTiret(logger) {
    Test.assertEqual(Fmt.delai(null, 1000), Fmt.DASH);
    Test.assertEqual(Fmt.delai(1000, null), Fmt.DASH);
    return true;
}

// --- Temps et retard, format du bloc "Temps d'acces" du cockpit ---------
//
// Ports EXACTS de formatTime et formatDelay (static/js/traffic.js). Les
// valeurs attendues ci-dessous sont celles que produit le cockpit, pas
// celles que produit ce code : c'est ce qui rend ces tests capables de
// detecter une DIVERGENCE, et non seulement une regression.

(:test)
function testDureeEnMinutesEtSecondes(logger) {
    Test.assertEqual(Fmt.duree(260), "4m 20s");
    Test.assertEqual(Fmt.duree(125), "2m 05s");   // seconde sur deux chiffres
    Test.assertEqual(Fmt.duree(1799), "29m 59s");
    return true;
}

(:test)
function testDureeSousUneMinuteResteEnSecondes(logger) {
    // Un troncon court se compte en secondes : l'afficher "0m 45s" ferait
    // lire un zero de tete la ou il n'y a rien a lire.
    Test.assertEqual(Fmt.duree(45), "45s");
    Test.assertEqual(Fmt.duree(0), "0s");
    return true;
}

(:test)
function testDureeOmetLesSecondesNulles(logger) {
    // "4m", pas "4m 00s" : c'est ce que fait le cockpit, et la corde d'une
    // ligne d'axe est comptee.
    Test.assertEqual(Fmt.duree(240), "4m");
    Test.assertEqual(Fmt.duree(60), "1m");
    return true;
}

(:test)
function testDureeInconnueEstUnTiret(logger) {
    Test.assertEqual(Fmt.duree(null), Fmt.DASH);
    return true;
}

(:test)
function testRetardToujoursAfficheMemeNul(logger) {
    // LA regle du cockpit : "+0s" est une information (retard connu, nul),
    // pas une absence. Le masquer confondrait un axe fluide avec un axe
    // dont on ignore le retard.
    Test.assertEqual(Fmt.retard(0), "+0s");
    return true;
}

(:test)
function testRetardEnSecondesPuisMinutes(logger) {
    Test.assertEqual(Fmt.retard(45), "+45s");
    Test.assertEqual(Fmt.retard(90), "+1m 30s");
    Test.assertEqual(Fmt.retard(120), "+2m");
    Test.assertEqual(Fmt.retard(480), "+8m");
    return true;
}

(:test)
function testRetardNegatifRameneAZero(logger) {
    // Le serveur ne rend jamais de negatif (max(0, ...) dans axes_mur),
    // mais formatDelay cote cockpit s'en protege aussi -- un axe plus
    // rapide que d'habitude affiche "+0s".
    Test.assertEqual(Fmt.retard(-30), "+0s");
    return true;
}

(:test)
function testRetardInconnuEstUnTiret(logger) {
    // Distinct de "+0s" : la source ne repond pas, on ne SAIT pas.
    Test.assertEqual(Fmt.retard(null), Fmt.DASH);
    return true;
}
