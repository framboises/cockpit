using Toybox.Test;
using Toybox.Graphics;
using Toybox.Time;
using Toybox.Application;
using Toybox.System;

// Aucun test n'exercait le chemin de DESSIN : les tests portaient tous sur les
// fonctions pures. Un deref sur un champ nul dans drawMain -- typiquement `pk`
// ou `pkt` absents -- ne se serait vu qu'a l'ecran, apres sideload.
//
// Ces tests ne jugent pas l'esthetique (la mise en page a ete mesuree
// separement, cf. DebordementTest.mc pour la verification geometrique) : ils
// verifient que le rendu ne leve pas, dans chaque etat que la montre peut
// reellement atteindre.

// dcDeTest() construisait un tampon 454x454 CODE EN DUR -- la taille du
// fenix 8 AMOLED, pas celle de l'app cible fenix8solar51mm (280x280 REEL,
// verifie via System.getDeviceSettings()). Consequence : ces tests
// prouvaient l'absence d'exception sur un ecran 2,6x plus grand en surface
// que le vrai, jamais le debordement -- Monkey C ne leve pas quand on
// dessine hors ecran. C'est precisement le defaut que ce fichier a laisse
// passer. dcDeTest() lit maintenant la taille REELLE, jamais une constante.
function dcDeTest() {
    var s = System.getDeviceSettings();
    var bmp = Graphics.createBufferedBitmap({:width => s.screenWidth,
                                              :height => s.screenHeight});
    return bmp.get().getDc();
}

(:test)
function testDessinModePastNeLevePas(logger) {
    // Aucune hypothese sur ce qu'un test precedent a laisse dans la seconde
    // cle Storage : drawMain lit maintenant aussi Cache.loadPages(), ce test
    // doit rester deterministe quel que soit l'ordre d'execution.
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new CockpitView();
    Cache.save({"t" => null, "rx" => Time.now().value(), "m" => "past",
                "n" => "LMC 26", "e" => null, "er" => null,
                "pk" => 52409, "pkt" => 1783175368,
                "w" => 24.2, "wl" => 0, "al" => []});
    vue.onFetched(false, null);
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinModePastSansPicNeLevePas(logger) {
    // Edition rapportee sans pic exploitable : le serveur l'evite, mais un
    // cache ecrit avant ce garde-fou peut encore porter ce cas.
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new CockpitView();
    Cache.save({"t" => null, "rx" => Time.now().value(), "m" => "past",
                "n" => null, "e" => null, "er" => null,
                "pk" => null, "pkt" => null,
                "w" => null, "wl" => 0, "al" => []});
    vue.onFetched(false, null);
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinModeLiveNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new CockpitView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "p" => 44980,
                "pk" => 39800, "pkt" => Time.now().value() - 5400,
                "w" => 27.4, "wl" => 1,
                "al" => [[3, "SOS tablette"], [2, "Vent 72 km/h"],
                         [1, "Ouverture imminente"]]});
    vue.onFetched(false, null);
    vue.onUpdate(dcDeTest());
    // Seconde page : la liste complete des alertes.
    vue.nextPage();
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinSansAucuneDonneeNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new CockpitView();
    vue.onUpdate(dcDeTest());
    return true;
}

// Les quatre tests qui suivent exercent les etats introduits par la page 1 :
// le heros en presents (pas le cumul d'entrees), les deux motifs du mode
// past avec leurs couleurs distinctes, et la bande de voyants -- presente et
// absente -- qui lit `Cache.loadPages()`/`Pages.bloc` (tache 8). Comme le
// reste de ce fichier, ils ne jugent pas l'esthetique : ils prouvent que ces
// chemins de dessin, atteignables en exploitation reelle, ne levent pas.

(:test)
function testDessinModeLiveAvecVoyantsNeLevePas(logger) {
    // Fiches PC org en instance ET trafic en tension : la bande de voyants
    // doit s'afficher, coloree par le pire (le trafic).
    var vue = new CockpitView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "p" => 44980,
                "pk" => 39800, "pkt" => Time.now().value() - 5400,
                "w" => 27.4, "wl" => 1, "al" => []});
    Cache.savePages({"mc" => {"s" => [2, 1], "sc" => [1, 0], "tq" => [0, 0],
                               "f" => [1, 0], "o" => [0, 0]},
                     "tr" => {"vd" => 2}, "me" => null, "st" => null});
    vue.onFetched(false, null);
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinVoyantsAbsentsQuandCalmeNeLevePas(logger) {
    // Des fiches closes et un trafic fluide : rien a signaler, la bande ne
    // doit pas s'afficher (et donc ne consommer aucune ligne).
    var vue = new CockpitView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "p" => 44980,
                "pk" => 39800, "pkt" => Time.now().value() - 5400,
                "w" => 27.4, "wl" => 1, "al" => []});
    Cache.savePages({"mc" => {"s" => [0, 3], "sc" => [0, 1], "tq" => [0, 0],
                               "f" => [0, 0], "o" => [0, 0]},
                     "tr" => {"vd" => 0}, "me" => null, "st" => null});
    vue.onFetched(false, null);
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinVoyantsMcPaireManquanteNeLevePas(logger) {
    // Correction 6 : si la forme du bloc mc change sans bump de
    // Cache.SCHEMA_PAGES (ici : la cle "s" absente), la page 0 -- celle qui
    // doit se lire en deux secondes -- ne doit JAMAIS lever dans onUpdate.
    // Avant le garde-fou (CockpitView.premierElement), le deref nu
    // mc["s"][0] + mc["sc"][0] + ... faisait tomber TOUTE la page sur cette
    // seule cle manquante. Retirer premierElement fait tomber ce test.
    var vue = new CockpitView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "p" => 44980,
                "pk" => 39800, "pkt" => Time.now().value() - 5400,
                "w" => 27.4, "wl" => 1, "al" => []});
    Cache.savePages({"mc" => {"sc" => [1, 0], "tq" => [0, 0],
                               "f" => [1, 0], "o" => [0, 0]},
                     "tr" => {"vd" => 2}, "me" => null, "st" => null});
    vue.onFetched(false, null);
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinVoyantsMcPaireVideNeLevePas(logger) {
    // Variante de la meme correction : la cle existe mais porte un tableau
    // VIDE plutot qu'absente -- un deref [0] nu leverait aussi la-dessus,
    // premierElement doit s'en proteger tout autant.
    var vue = new CockpitView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "p" => 44980,
                "pk" => 39800, "pkt" => Time.now().value() - 5400,
                "w" => 27.4, "wl" => 1, "al" => []});
    Cache.savePages({"mc" => {"s" => [], "sc" => [1, 0], "tq" => [0, 0],
                               "f" => [1, 0], "o" => [0, 0]},
                     "tr" => {"vd" => 2}, "me" => null, "st" => null});
    vue.onFetched(false, null);
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinSansBlocsPagesNeLevePas(logger) {
    // Aucun cache de pages disponible (jamais recu, ou source en panne sur
    // les quatre blocs) : Cache.loadPages() rend null, Pages.bloc doit
    // rendre null a son tour sans lever, et la bande doit rester absente.
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new CockpitView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "p" => 44980,
                "pk" => 39800, "pkt" => Time.now().value() - 5400,
                "w" => 27.4, "wl" => 1, "al" => []});
    vue.onFetched(false, null);
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinModePastMotifInactifNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new CockpitView();
    Cache.save({"t" => null, "rx" => Time.now().value(), "m" => "past",
                "mr" => "inactif", "n" => "LMC 26", "e" => null, "er" => null,
                "p" => null, "pk" => 52409, "pkt" => 1783175368,
                "w" => 24.2, "wl" => 0, "al" => []});
    vue.onFetched(false, null);
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinModePastMotifSansReleveNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new CockpitView();
    Cache.save({"t" => null, "rx" => Time.now().value(), "m" => "past",
                "mr" => "sans_releve", "n" => "LMC 26", "e" => null,
                "er" => null, "p" => null, "pk" => 52409, "pkt" => 1783175368,
                "w" => 24.2, "wl" => 0, "al" => []});
    vue.onFetched(false, null);
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinEditionsDansSesTroisEtats(logger) {
    var vue = new EditionsView();
    // Chargement.
    vue.onUpdate(dcDeTest());
    // Echec reseau.
    vue.onFetched(false, null);
    vue.onUpdate(dcDeTest());
    // Liste reelle, plus longue que ce qui tient a l'ecran.
    vue.onFetched(true, Mock.editions());
    vue.onUpdate(dcDeTest());
    // Defilement jusqu'en bas puis retour, pour eprouver les bornes.
    vue.scroll(5);
    vue.onUpdate(dcDeTest());
    vue.scroll(-99);
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinEditionsListeVideNeLevePas(logger) {
    var vue = new EditionsView();
    vue.onFetched(true, []);
    vue.onUpdate(dcDeTest());
    vue.scroll(1);
    return true;
}

// Les quatre tests qui suivent exercent MainCouranteView (tache 10) dans ses
// trois etats distincts (bloc present, bloc absent, mode past) plus le cas
// ou toutes les fiches sont a zero -- un etat que le bloc PRESENT peut
// legitimement porter (calme operationnel reel), a ne pas confondre avec le
// bloc absent qui rend des tirets. Comme le reste de ce fichier, ils ne
// jugent pas l'esthetique : ils prouvent que ces chemins de dessin ne
// levent pas.

(:test)
function testDessinMainCourantePresenteNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new MainCouranteView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "p" => 44980, "pk" => 39800, "pkt" => Time.now().value() - 5400,
                "w" => 27.4, "wl" => 1, "al" => []});
    Cache.savePages({"mc" => {"t" => Time.now().value() - 120,
                               "s" => [2, 14], "sc" => [1, 8], "tq" => [0, 3],
                               "f" => [1, 1], "o" => [3, 5]},
                     "tr" => null, "me" => null, "st" => null});
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinMainCouranteBlocAbsentNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new MainCouranteView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "p" => 44980, "pk" => 39800, "pkt" => Time.now().value() - 5400,
                "w" => 27.4, "wl" => 1, "al" => []});
    // Aucun Cache.savePages() : source des quatre blocs en panne cote
    // serveur, exactement le cas que Pages.bloc doit rendre en null.
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinMainCourantePastNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new MainCouranteView();
    Cache.save({"t" => null, "rx" => Time.now().value(), "m" => "past",
                "n" => "LMC 26", "e" => null, "er" => null,
                "pk" => 52409, "pkt" => 1783175368,
                "w" => 24.2, "wl" => 0, "al" => []});
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinMainCouranteTousCompteursAZeroNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new MainCouranteView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "p" => 44980, "pk" => 39800, "pkt" => Time.now().value() - 5400,
                "w" => 27.4, "wl" => 1, "al" => []});
    Cache.savePages({"mc" => {"t" => Time.now().value(),
                               "s" => [0, 0], "sc" => [0, 0], "tq" => [0, 0],
                               "f" => [0, 0], "o" => [0, 0]},
                     "tr" => null, "me" => null, "st" => null});
    vue.onUpdate(dcDeTest());
    return true;
}

// Les six tests qui suivent exercent TraficView (tache 11) dans ses trois
// etats (bloc present avec terrains, terrains vides, bloc absent), les
// quatre premiers parcourant chacun un des quatre verdicts -- c'est le SEUL
// endroit ou toute l'echelle 0..3 est parcourue en dessin, la fonction pure
// Pages.verdictMot etant deja couverte a part dans PagesTest.mc.

// Noms de terrain reels et longs, repris de test_watch_state.py::
// test_taille_du_payload_complet_sous_deux_ko (le pire cas plausible cote
// serveur) -- pas des noms inventes plus courts qui ne diraient rien du
// risque de debordement mesure a la sonde.
function trBlocPireCas(vd) {
    return {"t" => Time.now().value(), "vd" => vd, "ac" => 3, "z" => 27,
            "r" => [["Entree Houx paddock", "i", 24, 3],
                    ["Sortie Musee circuit", "o", 18, 2],
                    ["Rond point Maison Blanche", "i", 15, 2],
                    ["Parking Karting exterieur", "-", 6, 1]]};
}

(:test)
function testDessinTraficVerdictFluideNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new TraficView();
    Cache.savePages({"mc" => null, "tr" => trBlocPireCas(0), "me" => null,
                     "st" => null});
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinTraficVerdictVigilanceNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new TraficView();
    Cache.savePages({"mc" => null, "tr" => trBlocPireCas(1), "me" => null,
                     "st" => null});
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinTraficVerdictTensionNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new TraficView();
    Cache.savePages({"mc" => null, "tr" => trBlocPireCas(2), "me" => null,
                     "st" => null});
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinTraficVerdictCritiqueNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new TraficView();
    Cache.savePages({"mc" => null, "tr" => trBlocPireCas(3), "me" => null,
                     "st" => null});
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinTraficTerrainsVidesNeLevePas(logger) {
    // r vide : ce n'est pas une panne, Waze n'a juste rien remonte de
    // charge sur les axes surveilles -- distinct du bloc tr absent.
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new TraficView();
    Cache.savePages({"mc" => null,
                     "tr" => {"t" => Time.now().value(), "vd" => 0, "ac" => 0,
                              "z" => 0, "r" => []},
                     "me" => null, "st" => null});
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinTraficBlocAbsentNeLevePas(logger) {
    // Aucun Cache.savePages() : source des quatre blocs en panne cote
    // serveur, exactement le cas que Pages.bloc doit rendre en null.
    //
    // Exercice la regle des quatre pages : la structure reste FIXE (bandeau
    // verdict en tirets/gris, ligne de comptes en tirets, pied "indisponible"
    // en rouge) -- aucune ligne de terrain n'est inventee puisque leur
    // NOMBRE est inconnu quand la source est en panne. Distinct du cas
    // "terrains vides" (testDessinTraficTerrainsVidesNeLevePas), ou le
    // nombre est connu (zero) et le dit explicitement.
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new TraficView();
    vue.onUpdate(dcDeTest());
    return true;
}

// Les trois tests qui suivent verifient formatComptes en VALEUR (correction
// 5) : un compte ac/z INCONNU (alertes perimees ou source en panne, cf.
// watch_pages.build_trafic/ALERTES_MAX_AGE) doit rendre des tirets, jamais
// "0" -- qui se lirait comme un calme operationnel avere. Un test "ne leve
// pas" ne le detecterait jamais, la chaine produite ne leve rien dans les
// deux cas.

(:test)
function testFormatComptesTiretsQuandAlertesInconnu(logger) {
    var vue = new TraficView();
    Test.assertEqual(vue.formatComptes(null, null), "-- alerte . -- accident");
    return true;
}

(:test)
function testFormatComptesValeursConnuesEtPluriel(logger) {
    var vue = new TraficView();
    Test.assertEqual(vue.formatComptes(0, 0), "0 alerte . 0 accident");
    Test.assertEqual(vue.formatComptes(1, 1), "1 alerte . 1 accident");
    Test.assertEqual(vue.formatComptes(3, 2), "3 alertes . 2 accidents");
    return true;
}

(:test)
function testFormatComptesUneSeuleMoitieInconnue(logger) {
    // Cas mixte, improbable cote serveur (ac/z tombent ensemble) mais la
    // vue ne doit pas en dependre : chaque moitie se degrade
    // independamment.
    var vue = new TraficView();
    Test.assertEqual(vue.formatComptes(null, 2), "-- alerte . 2 accidents");
    Test.assertEqual(vue.formatComptes(5, null), "5 alertes . -- accident");
    return true;
}

// Le test qui suit verifie statutSeverite en VALEUR (correction 2) : les
// mots doivent etre ceux du MUR (circulation.html:590-601), pas ceux de
// trafic_etat.classify_congestion qui n'alimente plus cette vue depuis que
// severite_axe (double verrou) l'a remplace.

(:test)
function testStatutSeveriteReprendLesMotsDuMur(logger) {
    var vue = new TraficView();
    Test.assertEqual(vue.statutSeverite(0), "fluide");
    Test.assertEqual(vue.statutSeverite(1), "dense");
    Test.assertEqual(vue.statutSeverite(2), "ralenti");
    Test.assertEqual(vue.statutSeverite(3), "fort ralenti");
    Test.assertEqual(vue.statutSeverite(4), "bouchon");
    Test.assertEqual(vue.statutSeverite(null), "--");
    return true;
}

// Les cinq tests qui suivent exercent MeteoView (tache 12) : bloc absent, et
// les quatre combinaisons pluie x consigne (le bloc reste vivant toute
// l'annee, pas de mode past a part). Comme le reste de ce fichier, ils ne
// jugent pas l'esthetique : ils prouvent que ces chemins de dessin ne
// levent pas -- y compris le lisere de vigilance (vg >= 1) et le decoupage
// de la consigne sur deux lignes.

(:test)
function testDessinMeteoPluieEtConsigneNeLevePas(logger) {
    // Le pire cas plausible : pluie attendue, consigne au maximum des 68
    // caracteres (watch_pages.CONSIGNE_MAX, releve pour porter deux lignes
    // -- tache 7), et vigilance orange -- exerce le lisere colore ET le
    // decoupage de la consigne sur deux lignes.
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new MeteoView();
    Cache.savePages({"mc" => null, "tr" => null, "st" => null,
                     "me" => {"t" => Time.now().value() - 300, "tc" => 31.6,
                              "v" => 25.0, "rf" => 72.0, "pl" => 15,
                              "pm" => 8.0,
                              "cn" => "Palpation renforcee sur tous les postes nord et sud avant le depart.",
                              "cl" => 2, "vg" => 2}});
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinMeteoPluieSansConsigneNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new MeteoView();
    Cache.savePages({"mc" => null, "tr" => null, "st" => null,
                     "me" => {"t" => Time.now().value() - 300, "tc" => 22.0,
                              "v" => 18.0, "rf" => 30.0, "pl" => 40,
                              "pm" => 3.0, "cn" => null, "cl" => 0, "vg" => 0}});
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinMeteoSansPluieAvecConsigneNeLevePas(logger) {
    // Consigne active sans pluie a l'horizon (ex : palpation renforcee pour
    // une autre raison que la meteo elle-meme n'exprime pas) -- prouve que
    // la consigne ne depend pas de l'etat de la strate pluie.
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new MeteoView();
    Cache.savePages({"mc" => null, "tr" => null, "st" => null,
                     "me" => {"t" => Time.now().value() - 300, "tc" => 27.0,
                              "v" => 12.0, "rf" => 20.0, "pl" => null,
                              "pm" => null, "cn" => "Rafales 62 km/h - securiser les structures",
                              "cl" => 1, "vg" => 0}});
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinMeteoSansPluieSansConsigneNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new MeteoView();
    Cache.savePages({"mc" => null, "tr" => null, "st" => null,
                     "me" => {"t" => Time.now().value() - 1800, "tc" => 18.5,
                              "v" => 10.0, "rf" => 18.0, "pl" => null,
                              "pm" => null, "cn" => null, "cl" => 0, "vg" => 0}});
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinMeteoBlocAbsentNeLevePas(logger) {
    // Source en panne : structure fixe (titre, trois strates en tirets),
    // pied "indisponible" en rouge -- aucun lisere de vigilance (vg
    // inconnu, jamais affirmatif).
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new MeteoView();
    vue.onUpdate(dcDeTest());
    return true;
}

// Reprise mise en page (ecran REEL 280x280, fenix8solar51mm -- PAS 454, cf.
// rapport de tache) : CONSIGNE_MAX (watch_pages.py) passe de 68 a 66. Deux
// mesures successives, toutes deux invalidees par une relecture qui a
// trouve le MEME defaut de raisonnement sur l'axe des largeurs que celui
// qui a motive toute cette tache sur l'axe des ordonnees :
//
//   1) Premiere mesure (sonde) : dispo1=276,83 px, dispo2=267,72 px, en
//      passant `largeurUtile(dc, y)` -- la corde a l'ANCRE (le sommet du
//      bloc de texte), pas a son bord le plus eloigne du centre.
//   2) Relecture : sur un cadran rond, la corde RETRECIT en s'eloignant du
//      centre, donc c'est le bord du bloc [y, y+hauteur] le plus eloigne
//      qui contraint -- pas forcement l'ancre. `Pages.largeurUtile(dc, y,
//      hauteur)` corrige ce calcul dans toute l'app (cf. Pages.mc). A la
//      corde REELLE (dispo1=266,47 px a y=161/base=183, dispo2=250,05 px a
//      y=181/base=203, toutes deux plus etroites qu'annonce), la capacite
//      de couperConsigne en FONT_XTINY tombe de 68 a 66 caracteres --
//      verifie a la sonde (retiree apres usage) : n=66 tient ENTIER,
//      n=68 tronque desormais.
//
// Les DEUX consignes reelles les plus longues (foudre+grele 52 caracteres,
// WBGT danger 59 caracteres) tiennent toujours ENTIERES a 66, avec 7
// caracteres de marge au-dessus de la plus longue des deux (WBGT).
//
// Ce test rejoue les DEUX pires consignes REELLES du systeme (meteo_etat.py,
// foudre+grele 52 caracteres et WBGT danger 59 caracteres) contre le
// dispo1/dispo2 CORRIGES et verifie qu'aucune des deux lignes n'est
// tronquee -- CONSIGNE_MAX ne sert a rien si couperConsigne tronque quand
// meme la seconde ligne derriere lui.
(:test)
function testCouperConsigneNeTronquePasLesDeuxConsignesReelles(logger) {
    var dc = dcDeTest();
    var vue = new MeteoView();
    // Mesures via Pages.largeurUtile (bord contraignant, pas l'ancre),
    // pire cas (vigilance affichee, consigne demarre a y=161) : dispo1 a
    // y=161/base=183, dispo2 a y=181/base=203.
    var dispo1 = 266.47;
    var dispo2 = 250.05;

    var foudre = "Foudre prevue sur la zone avec grele — mise a l'abri";
    var lignesFoudre = vue.couperConsigne(dc, foudre, Graphics.FONT_XTINY,
                                          dispo1, dispo2);
    Test.assertEqual(lignesFoudre.size(), 2);
    Test.assertEqual(lignesFoudre[0] + " " + lignesFoudre[1], foudre);

    var wbgt = "WBGT 30.5 °C — Travail lourd a suspendre, rotations courtes";
    var lignesWbgt = vue.couperConsigne(dc, wbgt, Graphics.FONT_XTINY,
                                        dispo1, dispo2);
    Test.assertEqual(lignesWbgt.size(), 2);
    Test.assertEqual(lignesWbgt[0] + " " + lignesWbgt[1], wbgt);
    return true;
}

// Complement en VALEUR (pas seulement "ne leve pas") : CONSIGNE_MAX (66)
// doit survivre au decoupage ENTIER sur les deux lignes, au pire dispo
// mesure -- c'est la garantie que le plafond cote serveur et la capacite
// reelle de la vue restent alignes. Si l'un des deux bouge sans l'autre, ce
// test tombe.
(:test)
function testConsigneMaxTientEntierSurDeuxLignes(logger) {
    var dc = dcDeTest();
    var vue = new MeteoView();
    var dispo1 = 266.47;
    var dispo2 = 250.05;
    var alphabet = "Palpation renforcee sur tous les postes nord et sud avant le depart des courses prevues cet apres midi la";
    var texte = alphabet.substring(0, 66);
    var lignes = vue.couperConsigne(dc, texte, Graphics.FONT_XTINY,
                                    dispo1, dispo2);
    Test.assertEqual(lignes.size(), 2);
    Test.assertEqual(lignes[0] + " " + lignes[1], texte);
    return true;
}

// Les quatre tests qui suivent exercent FrequentationView (tache 12) : bloc
// present (avec et sans pic du jour deja releve), bloc absent en mode live,
// et le mode past qui court-circuite toute la page ("hors evenement",
// meme regle que MainCouranteView -- la comparaison au jour equivalent n'a
// pas d'objet hors saison, le serveur ne construit meme pas le bloc).

(:test)
function testDessinFrequentationPresenteNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new FrequentationView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 51200, "er" => 4100,
                "p" => 47850, "pk" => 44100, "pkt" => Time.now().value() - 3600,
                "w" => 29.1, "wl" => 2, "al" => []});
    Cache.savePages({"mc" => null, "tr" => null, "me" => null,
                     "st" => {"t" => Time.now().value() - 600, "pj" => 44100,
                              "ph" => "13h20", "n1" => 40000}});
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinFrequentationSansPicDuJourNeLevePas(logger) {
    // Bloc present mais aucun pic releve pour l'instant (tot dans la
    // journee) : `pj`/`ph`/`n1` a null alors que le bloc existe -- distinct
    // du bloc absent, Fmt.count/le repli sur DASH doivent le rendre sans
    // lever.
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new FrequentationView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 0, "er" => 0,
                "p" => 0, "pk" => 44100, "pkt" => Time.now().value() - 3600,
                "w" => 18.0, "wl" => 0, "al" => []});
    Cache.savePages({"mc" => null, "tr" => null, "me" => null,
                     "st" => {"t" => null, "pj" => null, "ph" => null,
                              "n1" => null}});
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinFrequentationBlocAbsentNeLevePas(logger) {
    // Source en panne sur "st" (live) : structure fixe en tirets, sauf le
    // pic d'edition qui vient du NOYAU (pk/pkt) et reste donc affiche.
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new FrequentationView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "p" => 44980, "pk" => 39800, "pkt" => Time.now().value() - 5400,
                "w" => 27.4, "wl" => 1, "al" => []});
    vue.onUpdate(dcDeTest());
    return true;
}

(:test)
function testDessinFrequentationPastNeLevePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new FrequentationView();
    Cache.save({"t" => null, "rx" => Time.now().value(), "m" => "past",
                "n" => "LMC 26", "e" => null, "er" => null,
                "pk" => 52409, "pkt" => 1783175368,
                "w" => 24.2, "wl" => 0, "al" => []});
    vue.onUpdate(dcDeTest());
    return true;
}

// Delta N-1 : le seul calcul non trivial de cette tache, verifie en VALEUR
// (pas seulement "ne leve pas") -- Test.assert(x == null), jamais
// Test.assertEqual(x, null) qui plante dans ce SDK.

(:test)
function testFrequentationDeltaPctPositifNeLevePas(logger) {
    var vue = new FrequentationView();
    // (44100 - 40000) / 40000 * 100 = 10.25 -> arrondi 10.
    Test.assertEqual(vue.calculDeltaPct(44100, 40000), 10);
    return true;
}

(:test)
function testFrequentationDeltaPctNegatifNeLevePas(logger) {
    var vue = new FrequentationView();
    // (36000 - 40000) / 40000 * 100 = -10.0 exact.
    Test.assertEqual(vue.calculDeltaPct(36000, 40000), -10);
    return true;
}

(:test)
function testFrequentationDeltaPctSansN1NeLevePas(logger) {
    var vue = new FrequentationView();
    Test.assert(vue.calculDeltaPct(44100, null) == null);
    return true;
}

(:test)
function testFrequentationDeltaPctN1ZeroNeLevePas(logger) {
    // n1 == 0 : division impossible, jamais un delta invente (pas de
    // division par zero, pas un delta enorme absurde).
    var vue = new FrequentationView();
    Test.assert(vue.calculDeltaPct(100, 0) == null);
    return true;
}

// formatEdition/formatEditionCourt en VALEUR : la forme longue devait
// deborder de pres de 50 px pres du pied (mesure au device, ecran
// 280x280) -- le repli vers la forme compacte est verifie ici en tant que
// CONTENU (les deux chaines), le fait qu'il tienne a l'ecran l'est a part
// dans DebordementTest.mc.
(:test)
function testFormatEditionEtFormeCompacte(logger) {
    var vue = new FrequentationView();
    Test.assertEqual(vue.formatEdition(148919, 1776517509),
                     "edition 148 919 . " + Fmt.day(1776517509) + " "
                     + Fmt.hour(1776517509));
    Test.assertEqual(vue.formatEditionCourt(148919, 1776517509),
                     "ed. 148 919 " + Fmt.day(1776517509) + " "
                     + Fmt.hour(1776517509));
    return true;
}

// Navigation (tache 13) : six pages en cycle, aiguillage inline dans
// CockpitView.onUpdate. Ces tests verifient une VALEUR (currentPage(), pas
// seulement l'absence d'exception) -- la lecon de la tache 12 est que
// "ne leve pas" n'aurait pas revele un aiguillage qui pointe vers la
// mauvaise page.

(:test)
function testNextPageBoucleSurSixEtRevientAZero(logger) {
    var vue = new CockpitView();
    Test.assertEqual(vue.currentPage(), 0);
    for (var i = 0; i < 5; i += 1) {
        vue.nextPage();
    }
    Test.assertEqual(vue.currentPage(), 5);
    // Le sixieme appel referme le cycle.
    vue.nextPage();
    Test.assertEqual(vue.currentPage(), 0);
    return true;
}

(:test)
function testPreviousPageDepuisZeroArriveACinq(logger) {
    // Symetrique de testNextPageBoucleSurSixEtRevientAZero : avec six pages,
    // BAS doit reculer d'une page, pas avancer de cinq. Avec deux pages
    // seulement (etat avant la tache 13), nextPage() et previousPage()
    // etaient indiscernables -- ce test mord si onPreviousPage() reste
    // cable sur nextPage() par erreur.
    var vue = new CockpitView();
    Test.assertEqual(vue.currentPage(), 0);
    vue.previousPage();
    Test.assertEqual(vue.currentPage(), 5);
    vue.previousPage();
    Test.assertEqual(vue.currentPage(), 4);
    return true;
}

(:test)
function testSetPagePoseLaPageDemandee(logger) {
    // Mecanisme utilise par SautMenuDelegate pour sauter directement a une
    // page choisie dans le menu, sans passer par le cycle.
    var vue = new CockpitView();
    vue.setPage(4);
    Test.assertEqual(vue.currentPage(), 4);
    return true;
}

(:test)
function testPageViewAiguilleVersLaBonneVue(logger) {
    // Verifie l'aiguillage en VALEUR (instanceof), pas seulement l'absence
    // d'exception -- un swap entre deux pages voisines (ex : meteo <->
    // frequentation) dessinerait sans lever, seul ce test le detecterait.
    var vue = new CockpitView();
    Test.assert(vue.pageView(2) instanceof MainCouranteView);
    Test.assert(vue.pageView(3) instanceof TraficView);
    Test.assert(vue.pageView(4) instanceof MeteoView);
    Test.assert(vue.pageView(5) instanceof FrequentationView);
    return true;
}

(:test)
function testDessinChaquePageNeLevePas(logger) {
    // Complement de testPageViewAiguilleVersLaBonneVue : couvre le risque de
    // deref nul (ex : une vue annexe jamais initialisee) sur les six pages,
    // dans les deux modes (live, teste ici via Cache.save "live" ci-dessous)
    // et past (deja couvert par les tests dedies plus haut pour les pages 0
    // et 2/5).
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new CockpitView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "p" => 44980, "pk" => 39800, "pkt" => Time.now().value() - 5400,
                "w" => 27.4, "wl" => 1, "al" => []});
    Cache.savePages({"mc" => {"s" => [2, 1], "sc" => [1, 0], "tq" => [0, 0],
                               "f" => [1, 0], "o" => [0, 0]},
                     "tr" => {"vd" => 1}, "me" => {"tc" => 22.0},
                     "st" => {"pj" => 40000, "ph" => "13h20", "n1" => 38000}});
    vue.onFetched(false, null);
    for (var p = 0; p < 6; p += 1) {
        vue.setPage(p);
        vue.onUpdate(dcDeTest());
    }
    return true;
}

// Menu de saut (SautMenu) : sept entrees dans l'ordre attendu, les six pages
// du cycle puis les editions -- verifie en VALEUR (id + libelle de chaque
// entree), pas seulement que la construction ne leve pas. Un oubli d'entree
// ou un mauvais ordre serait invisible a un simple "ne leve pas".

(:test)
function testSautMenuSeptEntreesDansLOrdre(logger) {
    var menu = new SautMenu();
    var labels = ["Tableau de bord", "Alertes", "Main courante", "Trafic",
                  "Meteo", "Frequentation", "Pics par edition"];
    var ids = [0, 1, 2, 3, 4, 5, -1];
    for (var i = 0; i < labels.size(); i += 1) {
        var item = menu.getItem(i);
        Test.assert(item != null);
        Test.assertEqual(item.getLabel(), labels[i]);
        Test.assertEqual(item.getId(), ids[i]);
    }
    // Rien au-dela de la septieme entree : les editions sont bien une
    // entree de CE menu, pas nichees dans un sous-menu supplementaire.
    Test.assert(menu.getItem(labels.size()) == null);
    return true;
}
