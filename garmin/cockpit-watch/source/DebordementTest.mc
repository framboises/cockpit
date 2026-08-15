using Toybox.Test;
using Toybox.Graphics;
using Toybox.System;
using Toybox.Application;
using Toybox.Time;
using Toybox.Math;

// Anti-debordement PERMANENT. Aucun test de dessin anterieur ne pouvait
// attraper le defaut qui a motive ce fichier : les sondes de mise en page de
// tout ce projet imposaient un tampon 454x454 (la taille du fenix 8 AMOLED)
// alors que l'app cible fenix8solar51mm, dont l'ecran mesure REELEMENT
// 280x280 (System.getDeviceSettings(), verifie a la sonde). Un rendu hors
// ecran ne LEVE jamais en Monkey C -- les tests "ne leve pas" (DessinTest.mc)
// ne voient donc rien, ils prouvent l'absence d'exception, pas la geometrie.
//
// RecordingDc ci-dessous se substitue au DC reel : il delegue les requetes
// de geometrie (largeurs de texte, hauteurs de police) a un DC reel construit
// sur la taille REELLE de l'ecran, mais enregistre chaque element dessine
// (rectangle englobant) plutot que de le peindre. Apres onUpdate(dc), on
// dispose de la liste exacte de ce que la vue a essaye d'afficher et ou --
// on peut alors verifier que rien ne sort de l'ecran et que rien ne
// chevauche le pied de page, sur l'ecran REEL, dans chaque etat atteignable.
//
// Verifie par sabotage (cf. rapport de tache) : deplacer une seule ligne
// d'une vue en dehors de sa position mesuree fait tomber le test
// correspondant.
class RecordingDc {

    hidden var mReal;
    hidden var mRects;
    hidden var mPenWidth;

    function initialize(real) {
        mReal = real;
        mRects = [];
        mPenWidth = 1;
    }

    function getWidth() { return mReal.getWidth(); }
    function getHeight() { return mReal.getHeight(); }
    function getFontHeight(font) { return mReal.getFontHeight(font); }
    function getTextWidthInPixels(text, font) {
        return mReal.getTextWidthInPixels(text, font);
    }

    // Pas de rendu reel : seule la geometrie compte pour ce test.
    function setColor(fg, bg) { }
    function clear() { }

    function setPenWidth(w) {
        mPenWidth = w;
    }

    // Anchre par le HAUT (jamais TEXT_JUSTIFY_VCENTER dans ce projet) :
    // un texte pose a y occupe [y, y + hauteur police], comme partout
    // ailleurs dans l'app.
    function drawText(x, y, font, text, justify) {
        var tw = mReal.getTextWidthInPixels(text, font);
        var th = mReal.getFontHeight(font);
        var x0;
        var x1;
        if (justify == Graphics.TEXT_JUSTIFY_RIGHT) {
            x0 = x - tw;
            x1 = x;
        } else if (justify == Graphics.TEXT_JUSTIFY_LEFT) {
            x0 = x;
            x1 = x + tw;
        } else {
            x0 = x - tw / 2.0;
            x1 = x + tw / 2.0;
        }
        mRects.add({"x0" => x0.toFloat(), "y0" => y.toFloat(),
                    "x1" => x1.toFloat(), "y1" => (y + th).toFloat(),
                    "label" => text});
    }

    // Les quatre icones (resources/drawables/mc_*.svg) mesurent 32x32 --
    // taille fixe connue de la source, pas mesuree ici (les ressources
    // bitmap chargees ne rendent pas de dimensions fiables a ce stade du
    // SDK).
    function drawBitmap(x, y, bitmap) {
        mRects.add({"x0" => x.toFloat(), "y0" => y.toFloat(),
                    "x1" => (x + 32).toFloat(), "y1" => (y + 32).toFloat(),
                    "label" => "<icone>"});
    }

    function drawCircle(x, y, r) {
        var rr = r + mPenWidth / 2.0;
        mRects.add({"x0" => (x - rr).toFloat(), "y0" => (y - rr).toFloat(),
                    "x1" => (x + rr).toFloat(), "y1" => (y + rr).toFloat(),
                    "label" => "<cercle>"});
    }

    function rects() { return mRects; }
}

// DC d'enregistrement, construit sur la taille REELLE de l'ecran --
// System.getDeviceSettings(), jamais une taille codee en dur (c'est
// precisement l'erreur que ce fichier existe pour empecher).
function dcEnregistrement() {
    var s = System.getDeviceSettings();
    var bmp = Graphics.createBufferedBitmap({:width => s.screenWidth,
                                              :height => s.screenHeight});
    return new RecordingDc(bmp.get().getDc());
}

// Ordonnee du sommet du pied de page, convention partagee par CockpitView
// (page 0), MainCouranteView, TraficView, MeteoView et FrequentationView :
// dc.getHeight() - hX - 17.
function piedStandard(dc) {
    return dc.getHeight() - dc.getFontHeight(Graphics.FONT_XTINY) - 17;
}

// Liste les violations en texte (pour le logger) plutot qu'un simple
// booleen : un test qui tombe doit dire OU et de COMBIEN, pas seulement
// "echec".
//
// `footBandTop` peut valoir null (EditionsView, page ALERTES : pas de
// convention de pied fixe) -- dans ce cas seul le debordement hors ecran est
// verifie.
function violationsDebordement(rects, width, height, footBandTop, eps) {
    var out = [];
    for (var i = 0; i < rects.size(); i += 1) {
        var r = rects[i];
        if (r["x0"] < 0 - eps || r["x1"] > width + eps ||
            r["y0"] < 0 - eps || r["y1"] > height + eps) {
            out.add("HORS ECRAN [" + r["label"] + "] x=" + r["x0"] + ".." +
                    r["x1"] + " y=" + r["y0"] + ".." + r["y1"] +
                    " (ecran " + width + "x" + height + ")");
        } else if (footBandTop != null && r["y0"] < footBandTop - eps &&
                   r["y1"] > footBandTop + eps) {
            out.add("CHEVAUCHE LE PIED [" + r["label"] + "] y=" + r["y0"] +
                    ".." + r["y1"] + " (pied a " + footBandTop + ")");
        }
    }
    return out;
}

function verifierNeDebordePas(logger, dc, footBandTop) {
    var violations = violationsDebordement(dc.rects(), dc.getWidth(),
                                            dc.getHeight(), footBandTop, 1.0);
    for (var i = 0; i < violations.size(); i += 1) {
        logger.debug(violations[i]);
    }
    Test.assertEqual(violations.size(), 0);
}

// ---------------------------------------------------------------------
// CockpitView, page 0 (tableau de bord) : pire cas plausible -- libelle
// d'evenement long + alertes, bande de voyants (MC en instance ET trafic en
// tension ET vigilance orange, coloree par le pire des trois), WBGT eleve.
// ---------------------------------------------------------------------

(:test)
function testDebordementCockpitPage0PireCasNeDeborgePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new CockpitView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 148919, "er" => 3200,
                "p" => 142622, "pk" => 148919, "pkt" => Time.now().value() - 5400,
                "w" => 32.6, "wl" => 2,
                "al" => [[3, "SOS tablette"], [2, "Vent 72 km/h"],
                         [1, "Ouverture imminente"]]});
    Cache.savePages({"mc" => {"s" => [2, 14], "sc" => [1, 8], "tq" => [0, 3],
                               "f" => [1, 1], "o" => [3, 5]},
                     "tr" => {"vd" => 2},
                     "me" => {"vg" => 2}, "st" => null});
    vue.onFetched(false, null);
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

(:test)
function testDebordementCockpitPage0ModePastNeDeborgePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new CockpitView();
    Cache.save({"t" => null, "rx" => Time.now().value(), "m" => "past",
                "mr" => "sans_releve", "n" => "24H CAMIONS 26", "e" => null,
                "er" => null, "pk" => 52409, "pkt" => 1783175368,
                "w" => 24.2, "wl" => 0, "al" => []});
    vue.onFetched(false, null);
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

// CockpitView, page 1 (ALERTES) : le serveur borne deja a 5
// (watch_state.MAX_ALERTS), labels jusqu'a 24 caracteres
// (watch_state.LABEL_MAX) -- pas de convention de pied fixe sur cette page,
// seul le debordement hors ecran est verifie.

(:test)
function testDebordementCockpitPage1PireCasNeDeborgePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new CockpitView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "p" => 44980, "pk" => 39800, "pkt" => Time.now().value() - 5400,
                "w" => 27.4, "wl" => 1,
                // Labels a la borne serveur reelle (watch_state.LABEL_MAX =
                // 24 caracteres) : au-dela, le serveur tronque deja avant
                // transport, la vue n'a jamais a en recevoir de plus longs.
                "al" => [[3, "SOS tablette Houx 5"],
                         [3, "Evacuation Beausejour"],
                         [2, "Vent rafale 72 km/h"],
                         [2, "Palpation postes sud"],
                         [1, "Ouverture imminente"]]});
    vue.onFetched(false, null);
    vue.nextPage();
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, null);
    return true;
}

// ---------------------------------------------------------------------
// MainCouranteView : pire cas de compteurs a deux chiffres.
// ---------------------------------------------------------------------

(:test)
function testDebordementMainCourantePireCasNeDeborgePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new MainCouranteView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "p" => 44980, "pk" => 39800, "pkt" => Time.now().value() - 5400,
                "w" => 27.4, "wl" => 1, "al" => []});
    Cache.savePages({"mc" => {"t" => Time.now().value() - 120,
                               "s" => [12, 84], "sc" => [21, 108],
                               "tq" => [7, 63], "f" => [15, 41],
                               "o" => [23, 95]},
                     "tr" => null, "me" => null, "st" => null});
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

// ---------------------------------------------------------------------
// TraficView : pire cas -- quatre terrains au nom Waze reel long
// (jusqu'a 26 caracteres, cf. test_watch_state.py), tries par gravite
// decroissante, verdict CRITIQUE.
// ---------------------------------------------------------------------

(:test)
function testDebordementTraficPireCasNeDeborgePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new TraficView();
    Cache.savePages({"mc" => null, "tr" => trBlocPireCas(3), "me" => null,
                     "st" => null});
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

// Contenu, pas seulement geometrie : sur quatre terrains recus, seuls DEUX
// sont affiches (budget vertical mesure) -- les deux masques ne doivent
// jamais disparaitre en silence, ils doivent apparaitre en toutes lettres
// dans la ligne de comptes ("+2 axes"). Un test purement geometrique ne
// verrait jamais une regression qui remplacerait ce mecanisme par un
// silence -- la page continuerait de tenir dans l'ecran.
(:test)
function testTraficMentionneLesTerrainsMasques(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new TraficView();
    Cache.savePages({"mc" => null, "tr" => trBlocPireCas(3), "me" => null,
                     "st" => null});
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    var rects = dc.rects();
    var trouve = false;
    for (var i = 0; i < rects.size(); i += 1) {
        var label = rects[i]["label"];
        if (label != null && label.find("+2 axes") != null) {
            trouve = true;
        }
    }
    Test.assert(trouve);
    return true;
}

(:test)
function testDebordementTraficBlocAbsentNeDeborgePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new TraficView();
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

// ---------------------------------------------------------------------
// MeteoView : pire cas -- consigne au maximum de CONSIGNE_MAX caracteres,
// pluie attendue, vigilance orange (mot + cercle).
// ---------------------------------------------------------------------

(:test)
function testDebordementMeteoPireCasNeDeborgePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new MeteoView();
    Cache.savePages({"mc" => null, "tr" => null, "st" => null,
                     "me" => {"t" => Time.now().value() - 300, "tc" => 31.6,
                              "v" => 25.0, "rf" => 72.0, "pl" => 15,
                              "pm" => 8.0,
                              "cn" => "Palpation renforcee sur tous les postes nord et sud avant le d",
                              "cl" => 2, "vg" => 2}});
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

(:test)
function testDebordementMeteoDeuxConsignesReellesNeDeborgePas(logger) {
    // Les deux pires consignes REELLES (meteo_etat.py), rejouees en
    // conditions de dessin completes -- pas seulement couperConsigne en
    // valeur (deja fait dans DessinTest.mc), ici c'est la PAGE ENTIERE qui
    // est verifiee, vigilance rouge en plus.
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new MeteoView();
    Cache.savePages({"mc" => null, "tr" => null, "st" => null,
                     "me" => {"t" => Time.now().value() - 300, "tc" => 29.0,
                              "v" => 30.0, "rf" => 65.0, "pl" => 5, "pm" => 12.0,
                              "cn" => "Foudre prevue sur la zone avec grele — mise a l'abri",
                              "cl" => 3, "vg" => 3}});
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));

    Cache.savePages({"mc" => null, "tr" => null, "st" => null,
                     "me" => {"t" => Time.now().value() - 300, "tc" => 34.0,
                              "v" => 8.0, "rf" => 14.0, "pl" => null, "pm" => null,
                              "cn" => "WBGT 30.5 °C — Travail lourd a suspendre, rotations courtes",
                              "cl" => 2, "vg" => 1}});
    var dc2 = dcEnregistrement();
    vue.onUpdate(dc2);
    verifierNeDebordePas(logger, dc2, piedStandard(dc2));
    return true;
}

(:test)
function testDebordementMeteoBlocAbsentNeDeborgePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new MeteoView();
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

// ---------------------------------------------------------------------
// FrequentationView : pire cas -- six chiffres partout.
// ---------------------------------------------------------------------

(:test)
function testDebordementFrequentationPireCasNeDeborgePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new FrequentationView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 148919, "er" => 12400,
                "p" => 142622, "pk" => 148919, "pkt" => Time.now().value() - 3600,
                "w" => 29.1, "wl" => 2, "al" => []});
    Cache.savePages({"mc" => null, "tr" => null, "me" => null,
                     "st" => {"t" => Time.now().value() - 600, "pj" => 142622,
                              "ph" => "16h15", "n1" => 138600}});
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

// ---------------------------------------------------------------------
// EditionsView : pire cas -- PAR_ECRAN entrees pleines, libelles/pics/dates
// reels (Mock.editions()), reperes de defilement hors et dans les bornes.
// Pas de convention de pied fixe : seul le debordement hors ecran est
// verifie, plus les reperes de defilement doivent rester DANS l'ecran.
// ---------------------------------------------------------------------

(:test)
function testDebordementEditionsPireCasNeDeborgePas(logger) {
    var vue = new EditionsView();
    vue.onFetched(true, Mock.editions());
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, null);

    vue.scroll(5);
    var dc2 = dcEnregistrement();
    vue.onUpdate(dc2);
    verifierNeDebordePas(logger, dc2, null);
    return true;
}
