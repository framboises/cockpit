using Toybox.Test;
using Toybox.Graphics;
using Toybox.System;
using Toybox.Application;
using Toybox.Time;
using Toybox.Math;
using Toybox.Position;

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
// on peut alors verifier que rien ne sort du VERRE ROND (pas seulement du
// carre qui l'entoure) et que rien ne chevauche le pied de page, sur
// l'ecran REEL, dans chaque etat atteignable.
//
// Premiere version de ce fichier (relecture) : la verification ne comparait
// les rectangles qu'au CARRE [0,largeur]x[0,hauteur], jamais au disque
// inscrit -- exactement le meme defaut de raisonnement que celui qui a
// motive toute cette tache, mais sur l'axe des largeurs plutot que des
// ordonnees (cf. largeurUtile dans Pages.mc). Un texte pouvait donc rester
// dans le carre tout en debordant du verre rond aux quatre coins du cadran,
// sans qu'aucune violation ne soit relevee -- cas concret trouve a la
// relecture : la bande de voyants de CockpitView (page 0) se dessinait sans
// aucun controle de largeur a une ordonnee ou la corde vaut 260,8 px.
// `depassementCadran` verifie desormais les QUATRE COINS de chaque
// rectangle contre le disque, la seule contrainte reelle sur un cadran
// rond.
//
// Verifie par sabotage (cf. rapport de tache) : deplacer une seule ligne
// d'une vue en dehors de sa position mesuree, OU reintroduire un appel a
// largeurUtile sans la hauteur du bloc, fait tomber le test correspondant.
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

    // La fleche de la page Guidage est un polygone plein. Sans cette
    // methode, RecordingDc leverait -- et surtout, la fleche echapperait
    // entierement au controle geometrique, alors que c'est le plus grand
    // element dessine de toute l'app.
    function fillPolygon(points) {
        var x0 = points[0][0];
        var y0 = points[0][1];
        var x1 = x0;
        var y1 = y0;
        for (var i = 1; i < points.size(); i += 1) {
            if (points[i][0] < x0) { x0 = points[i][0]; }
            if (points[i][0] > x1) { x1 = points[i][0]; }
            if (points[i][1] < y0) { y0 = points[i][1]; }
            if (points[i][1] > y1) { y1 = points[i][1]; }
        }
        mRects.add({"x0" => x0.toFloat(), "y0" => y0.toFloat(),
                    "x1" => x1.toFloat(), "y1" => y1.toFloat(),
                    "label" => "<fleche>"});
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

// Distance du coin le plus eloigne du centre du cadran, moins le rayon.
// Positif = ce coin deborde du VERRE ROND ; negatif ou nul = a l'interieur.
// Les quatre coins du rectangle englobant sont testes individuellement --
// PAS une comparaison au carre [0,largeur]x[0,hauteur], que la premiere
// version de ce fichier utilisait. Cette premiere version laissait passer
// tout rectangle reste dans le carre mais sorti du disque inscrit (les
// quatre coins du cadran, hors du verre sur une montre ronde) -- c'est
// exactement ce que largeurUtile existe pour empecher cote production, et
// ce test doit verifier la MEME contrainte, pas une approximation plus
// laxiste. Le carre est en fait un cas particulier inutile ici : sur un
// cadran rond, le disque inscrit (rayon = largeur/2 = hauteur/2) est
// TOUJOURS strictement contenu dans le carre sauf aux quatre points de
// tangence -- rester dans le disque garantit deja de rester dans le carre.
function depassementCadran(r, cx, cy, rayon) {
    var coins = [[r["x0"], r["y0"]], [r["x1"], r["y0"]],
                 [r["x0"], r["y1"]], [r["x1"], r["y1"]]];
    var pire = -1.0;
    for (var i = 0; i < coins.size(); i += 1) {
        var dx = coins[i][0] - cx;
        var dy = coins[i][1] - cy;
        var dist = Math.sqrt(dx * dx + dy * dy);
        if (dist > pire) {
            pire = dist;
        }
    }
    return pire - rayon;
}

// Liste les violations en texte (pour le logger) plutot qu'un simple
// booleen : un test qui tombe doit dire OU et de COMBIEN, pas seulement
// "echec".
//
// `footBandTop` peut valoir null (EditionsView, page ALERTES : pas de
// convention de pied fixe) -- dans ce cas seul le debordement du verre rond
// est verifie.
function violationsDebordement(rects, width, height, footBandTop, eps) {
    var out = [];
    var cx = width / 2.0;
    var cy = height / 2.0;
    var rayon = width / 2.0;
    for (var i = 0; i < rects.size(); i += 1) {
        var r = rects[i];
        var depassement = depassementCadran(r, cx, cy, rayon);
        if (depassement > eps) {
            out.add("HORS DU VERRE ROND [" + r["label"] + "] x=" + r["x0"] +
                    ".." + r["x1"] + " y=" + r["y0"] + ".." + r["y1"] +
                    " depasse de " + depassement + " px (rayon " + rayon + ")");
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

// Preuve directe, independante de toute vue : depassementCadran verifie le
// DISQUE inscrit, pas le carre qui l'entoure. Un rectangle peut rester
// entierement dans [0,280]x[0,280] -- ce que l'ancienne version de ce
// fichier verifiait -- tout en debordant aux quatre coins du cadran rond.
// Le coin (280,0) d'un rectangle colle au coin superieur droit de l'ecran
// est a sqrt(140^2+140^2)=197,99 px du centre (140,140), largement au-dela
// du rayon (140) -- hors du verre, dans le carre.
(:test)
function testDepassementCadranDetecteUnCoinDeCarreHorsDuCercle(logger) {
    var r = {"x0" => 260.0, "y0" => 0.0, "x1" => 280.0, "y1" => 20.0,
             "label" => "coin"};
    var depassement = depassementCadran(r, 140.0, 140.0, 140.0);
    Test.assert(depassement > 50.0);
    return true;
}

(:test)
function testDepassementCadranToleereUnRectangleCentre(logger) {
    // Contre-epreuve : un rectangle proche du centre, meme large, ne
    // deborde pas -- depassementCadran ne doit pas etre systematiquement
    // punitif.
    var r = {"x0" => 40.0, "y0" => 130.0, "x1" => 240.0, "y1" => 150.0,
             "label" => "centre"};
    var depassement = depassementCadran(r, 140.0, 140.0, 140.0);
    Test.assert(depassement <= 0.0);
    return true;
}

// EditionsView n'a pas de pied fixe, mais un repere de defilement bas ("v",
// dc.getHeight() - hX - 12) qui joue le meme role : rien ne doit s'en
// approcher au point de le chevaucher. Mesure a la sonde : la 3e entree
// finissait a 244, le repere a 246 -- 2 px d'ecart, jamais verifie
// jusqu'ici. `null` si le repere n'est pas affiche (rien en dessous) :
// rien a verifier dans ce cas.
function verifierPasDeChevauchementRepere(logger, dc) {
    var rects = dc.rects();
    var yRepere = null;
    for (var i = 0; i < rects.size(); i += 1) {
        if (rects[i]["label"] != null && rects[i]["label"].equals("v")) {
            yRepere = rects[i]["y0"];
        }
    }
    if (yRepere == null) {
        return;
    }
    var eps = 1.0;
    for (var i = 0; i < rects.size(); i += 1) {
        var r = rects[i];
        if (r["label"] != null && r["label"].equals("v")) {
            continue;
        }
        if (r["y1"] > yRepere + eps) {
            logger.debug("CHEVAUCHE LE REPERE DE DEFILEMENT [" + r["label"]
                         + "] y1=" + r["y1"] + " (repere a " + yRepere + ")");
        }
        Test.assert(r["y1"] <= yRepere + eps);
    }
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
    // Pire cas REEL de la bande de voyants (trouve manquant a la relecture :
    // la fixture precedente rendait "MC 7", un seul chiffre, jamais le
    // compteur a deux chiffres plausible en pleine affluence) : MC a deux
    // chiffres, verdict trafic CRITIQUE, vigilance ROUGE -- "MC 78   CRITIQUE   VIG ROUGE".
    Cache.savePages({"mc" => {"s" => [12, 14], "sc" => [21, 8], "tq" => [7, 3],
                               "f" => [15, 1], "o" => [23, 5]},
                     "tr" => {"vd" => 3},
                     "me" => {"vg" => 3}, "st" => null});
    vue.onFetched(false, null);
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

// Complement : le pire cas REALISTE ci-dessus ne force pas la troncature
// (26,8 corde disponible suffit), donc a lui seul il ne prouve pas que le
// filet de securite ajoute a la relecture (ajusterTexte sur la bande de
// voyants) fonctionne reellement -- seulement qu'il ne CASSE rien. Ce test
// force deliberement un MC a six chiffres (au-dela de tout plausible) pour
// exercer effectivement le chemin de troncature et prouver qu'il reste dans
// le verre rond.
(:test)
function testDebordementCockpitPage0BandeForceLaTroncatureNeDeborgePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new CockpitView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 148919, "er" => 3200,
                "p" => 142622, "pk" => 148919, "pkt" => Time.now().value() - 5400,
                "w" => 32.6, "wl" => 2, "al" => []});
    Cache.savePages({"mc" => {"s" => [999999, 0], "sc" => [0, 0],
                               "tq" => [0, 0], "f" => [0, 0], "o" => [0, 0]},
                     "tr" => {"vd" => 3}, "me" => {"vg" => 3}, "st" => null});
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
                // Labels a la borne serveur EXACTE (watch_state.LABEL_MAX =
                // 24 caracteres, verifie ci-dessous) -- le vrai pire cas,
                // pas une approximation plus courte. Au-dela de 24, le
                // serveur tronque deja avant transport.
                "al" => [[3, "SOS tablette Houx 5 nord"],
                         [3, "Evacuation Beausejour 12"],
                         [2, "Vent rafale 72 km/h fort"],
                         [2, "Palpation postes sud A12"],
                         [1, "Ouverture imminente Nord"]]});
    // Verifie que le fixture ci-dessus est bien au pire cas annonce (24
    // caracteres), pas une approximation : un test qui pretend jouer le
    // pire cas doit le prouver, pas seulement l'affirmer en commentaire.
    var al = Cache.load()["al"];
    for (var i = 0; i < al.size(); i += 1) {
        Test.assertEqual(al[i][1].length(), 24);
    }
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

// TOUTES les sous-pages du livret, pas seulement la premiere : la page
// trafic n'est plus un ecran mais un livret feuillete a START, et un
// debordement sur la troisieme page d'axes serait aussi invisible qu'un
// debordement sur la premiere.
(:test)
function testDebordementTraficToutesLesSousPagesNeDeborgentPas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new TraficView();
    Cache.savePages({"mc" => null, "tr" => trBlocPireCas(3), "me" => null,
                     "st" => null});
    var total = vue.nbSousPages();
    // Le fixture est au plafond serveur (18 axes) : il DOIT produire le
    // bilan plus trois ecrans d'axes. Sans cette verification, une
    // regression qui ramenerait le livret a une seule page ferait passer la
    // boucle ci-dessous sans rien couvrir.
    Test.assertEqual(total, 4);
    for (var i = 0; i < total; i += 1) {
        var dc = dcEnregistrement();
        vue.onUpdate(dc);
        verifierNeDebordePas(logger, dc, piedStandard(dc));
        vue.sousPageSuivante();
    }
    return true;
}

// Contenu, pas seulement geometrie : la derniere sous-page ne contient que
// six axes sur dix-huit, et rien dans un test geometrique ne verrait une
// regression qui perdrait les douze autres -- la page continuerait de tenir
// dans l'ecran. Le compteur du pied ("4/4") et l'entete ("AXES 13-18 / 18")
// sont ce qui dit a l'utilisateur ou il en est.
(:test)
function testTraficNommeSaPositionDansLeLivret(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new TraficView();
    Cache.savePages({"mc" => null, "tr" => trBlocPireCas(3), "me" => null,
                     "st" => null});
    vue.sousPageSuivante();
    vue.sousPageSuivante();
    vue.sousPageSuivante();
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    var rects = dc.rects();
    var entete = false;
    var compteur = false;
    for (var i = 0; i < rects.size(); i += 1) {
        var label = rects[i]["label"];
        if (label == null) { continue; }
        if (label.find("AXES 13-18 / 18") != null) { entete = true; }
        if (label.find("4/4") != null) { compteur = true; }
    }
    Test.assert(entete);
    Test.assert(compteur);
    return true;
}

// Le nom d'axe est le seul element elastique de la ligne : il doit ceder la
// place au temps, au retard et au badge, jamais l'inverse. Un temps tronque
// serait un chiffre FAUX ("2" pour "24"), pas un mot abrege -- et rien dans
// un controle geometrique ne distingue les deux.
(:test)
function testTraficNeTronqueJamaisLesChiffres(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new TraficView();
    // Nom absurdement long ET badge ET retard a deux chiffres : la ligne ne
    // peut tenir qu'en rognant le nom.
    Cache.savePages({"mc" => null,
                     "tr" => {"t" => Time.now().value(), "vd" => 3, "ac" => 1,
                              "jm" => 0, "hz" => 0, "z" => 1,
                              "r" => [["Rond point de la Maison Blanche cote sud",
                                       "i", 24, 4, 1, 17]]},
                     "me" => null, "st" => null});
    vue.sousPageSuivante();
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    var rects = dc.rects();
    var temps = false;
    var retard = false;
    var badge = false;
    for (var i = 0; i < rects.size(); i += 1) {
        var label = rects[i]["label"];
        if (label == null) { continue; }
        if (label.equals("24'")) { temps = true; }
        if (label.equals("+17")) { retard = true; }
        if (label.equals("ACC")) { badge = true; }
    }
    Test.assert(temps);
    Test.assert(retard);
    Test.assert(badge);
    return true;
}

(:test)
function testDebordementTraficBilanSansAxeNeDeborgePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new TraficView();
    Cache.savePages({"mc" => null,
                     "tr" => {"t" => Time.now().value(), "vd" => 0, "ac" => 0,
                              "jm" => 0, "hz" => 0, "z" => 0, "r" => []},
                     "me" => null, "st" => null});
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

// Comptes INCONNUS (alertes perimees) : le bilan passe en tirets. Trois
// lignes de tirets sont plus courtes que trois comptes, mais la ligne du
// pire axe, elle, reste pleine -- le cas merite sa propre verification.
(:test)
function testDebordementTraficComptesInconnusNeDeborgePas(logger) {
    Application.Storage.deleteValue(Cache.KEY_PAGES);
    var vue = new TraficView();
    Cache.savePages({"mc" => null,
                     "tr" => {"t" => Time.now().value(), "vd" => null,
                              "ac" => null, "jm" => null, "hz" => null,
                              "z" => null,
                              "r" => [["Rond point Maison Blanche", "i", 24,
                                       4, null, 17]]},
                     "me" => null, "st" => null});
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    vue.sousPageSuivante();
    var dc2 = dcEnregistrement();
    vue.onUpdate(dc2);
    verifierNeDebordePas(logger, dc2, piedStandard(dc2));
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
    verifierPasDeChevauchementRepere(logger, dc);

    vue.scroll(5);
    var dc2 = dcEnregistrement();
    vue.onUpdate(dc2);
    verifierNeDebordePas(logger, dc2, null);
    verifierPasDeChevauchementRepere(logger, dc2);
    return true;
}


// ---------------------------------------------------------------------
// GuidageView : les quatre etats de la page, et la fleche dans TOUTES les
// directions. Un triangle tourne autour de son centre : c'est a 45 degres
// que sa boite englobante est la plus large, pas a 0 -- verifier une seule
// orientation ne prouverait rien.
// ---------------------------------------------------------------------

(:test)
function testDebordementGuidageSansPointNeDeborgePas(logger) {
    guidagePages(null);
    var vue = new GuidageView();
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, null);
    return true;
}

(:test)
function testDebordementGuidageRechercheGpsNeDeborgePas(logger) {
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

(:test)
function testDebordementGuidageSansCapNeDeborgePas(logger) {
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    vue.injecterPourTest([47.9493, 0.2214], null, Position.QUALITY_GOOD);
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

(:test)
function testDebordementGuidageFlecheDansToutesLesDirections(logger) {
    // Trente-six orientations, tous les dix degres : la boite englobante
    // d'un triangle tourne varie avec l'angle, et n'est maximale ni a 0 ni
    // a 90 degres.
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    for (var cap = 0; cap < 360; cap += 10) {
        vue.injecterPourTest([47.9493, 0.2214], cap.toFloat(),
                             Position.QUALITY_GOOD);
        var dc = dcEnregistrement();
        vue.onUpdate(dc);
        verifierNeDebordePas(logger, dc, piedStandard(dc));
    }
    return true;
}

(:test)
function testDebordementGuidageNomLongEtDistanceLongueNeDeborgentPas(logger) {
    // Pire cas de largeur : libelle au maximum autorise cote serveur
    // (watch_guidage.LABEL_MAX = 24) et distance a deux chiffres de km.
    guidagePages({"lat" => 48.857, "lon" => 2.352,
                  "n" => "Rond point Maison Blan", "s" => 9,
                  "t" => 1786000000});
    var vue = new GuidageView();
    vue.injecterPourTest([47.9493, 0.2214], 45.0, Position.QUALITY_POOR);
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

// Contenu, pas seulement geometrie : la page doit DIRE ce qu'elle sait.
// Une regression qui supprimerait le mot de qualite laisserait la couleur
// seule porter l'information -- ce que ce projet s'interdit partout.
(:test)
function testGuidageNommeLaQualiteDuFix(logger) {
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    vue.injecterPourTest([47.9493, 0.2214], 0.0, Position.QUALITY_POOR);
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    var rects = dc.rects();
    var trouve = false;
    for (var i = 0; i < rects.size(); i += 1) {
        var label = rects[i]["label"];
        if (label != null && label.find("GPS faible") != null) { trouve = true; }
    }
    Test.assert(trouve);
    return true;
}

(:test)
function testGuidageDitQueLaBoussoleManque(logger) {
    // Distinct de << recherche GPS >> : on SAIT ou l'on est, c'est
    // l'orientation du poignet qui manque. Les deux se corrigent
    // differemment (attendre vs bouger le bras).
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    vue.injecterPourTest([47.9493, 0.2214], null, Position.QUALITY_GOOD);
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    var rects = dc.rects();
    var trouve = false;
    for (var i = 0; i < rects.size(); i += 1) {
        var label = rects[i]["label"];
        if (label != null && label.find("boussole") != null) { trouve = true; }
    }
    Test.assert(trouve);
    return true;
}

(:test)
function testGuidageNeDessinePasDeFlecheSansCap(logger) {
    // LA regression a tenir, et elle est INVISIBLE a un controle
    // geometrique : une fleche pointant le nord par defaut tient
    // parfaitement dans l'ecran. Seule la liste de ce qui est dessine le
    // dit.
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    vue.injecterPourTest([47.9493, 0.2214], null, Position.QUALITY_GOOD);
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    var rects = dc.rects();
    for (var i = 0; i < rects.size(); i += 1) {
        var label = rects[i]["label"];
        Test.assert(label == null || !label.equals("<fleche>"));
    }
    return true;
}

(:test)
function testGuidageDessineLaFlecheDesQueLeCapArrive(logger) {
    // Contre-epreuve du test precedent : sans elle, supprimer purement et
    // simplement la fleche ferait passer les deux.
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    vue.injecterPourTest([47.9493, 0.2214], 0.0, Position.QUALITY_GOOD);
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    var rects = dc.rects();
    var trouve = false;
    for (var i = 0; i < rects.size(); i += 1) {
        var label = rects[i]["label"];
        if (label != null && label.equals("<fleche>")) { trouve = true; }
    }
    Test.assert(trouve);
    return true;
}


// --- Le pied de page n'est jamais tronque -------------------------------
//
// Un texte coupe tient PARFAITEMENT dans l'ecran : aucun controle
// geometrique ne le signale. La premiere redaction de ce pied affichait
// "GPS faible  .  STA" et "boussole indispo" -- trouve a la sonde, jamais
// par un test. Ces tests-ci comparent le texte REELLEMENT dessine au texte
// entier attendu.

function labelDuPied(dc) {
    // Le pied est le dernier texte dessine, et le seul dont l'ordonnee
    // vaut piedStandard.
    var rects = dc.rects();
    var y = piedStandard(dc);
    for (var i = 0; i < rects.size(); i += 1) {
        if ((rects[i]["y0"] - y).abs() < 1.0) {
            return rects[i]["label"];
        }
    }
    return null;
}

(:test)
function testPiedGuidageEntierRechercheGps(logger) {
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    Test.assertEqual(labelDuPied(dc), "recherche GPS");
    return true;
}

(:test)
function testPiedGuidageEntierSansBoussole(logger) {
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    vue.injecterPourTest([47.9493, 0.2214], null, Position.QUALITY_GOOD);
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    Test.assertEqual(labelDuPied(dc), "sans boussole");
    return true;
}

(:test)
function testPiedGuidageEntierPourChaqueQualite(logger) {
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    var qualites = [Position.QUALITY_GOOD, Position.QUALITY_USABLE,
                    Position.QUALITY_POOR];
    var mots = ["GPS bon", "GPS moyen", "GPS faible"];
    for (var i = 0; i < qualites.size(); i += 1) {
        vue.injecterPourTest([47.9493, 0.2214], 0.0, qualites[i]);
        var dc = dcEnregistrement();
        vue.onUpdate(dc);
        Test.assertEqual(labelDuPied(dc), mots[i]);
    }
    return true;
}

(:test)
function testPiedGuidageEntierApresEnregistrement(logger) {
    guidagePages(GD_HOUX);
    var vue = new GuidageView();
    vue.injecterPourTest([47.9493, 0.2214], 0.0, Position.QUALITY_GOOD);
    vue.enregistrerWaypoint();
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    Test.assertEqual(labelDuPied(dc), "enregistre");
    return true;
}

(:test)
function testEcranVideEnseigneStartEnEntier(logger) {
    // L'indice sur START vit sur l'ecran vide, ou la corde est large. Il ne
    // doit pas y etre tronque non plus.
    guidagePages(null);
    var vue = new GuidageView();
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    var rects = dc.rects();
    var trouve = false;
    for (var i = 0; i < rects.size(); i += 1) {
        var label = rects[i]["label"];
        if (label != null && label.equals("START l'enregistrera")) {
            trouve = true;
        }
    }
    Test.assert(trouve);
    verifierNeDebordePas(logger, dc, null);
    return true;
}


// Bornes de troncature SERVEUR (watch_timeline.ACTIVITE_MAX = 30,
// LIEU_MAX = 24) : au-dela, le serveur coupe deja. Ecrites en toutes
// lettres -- Monkey C n'a pas d'operateur de repetition de chaine, et un
// libelle plus court ne serait pas le pire cas.
const TL_ACTIVITE_MAX = "Ouverture des tribunes nord es";
const TL_LIEU_MAX = "SINGHER, SOMMER, DURAND,";

// ---------------------------------------------------------------------
// TimelineView : le heros et les ecrans de liste, plus les etats de
// chargement. Les libelles viennent du timetable, donc d'une source que
// cette app ne controle pas -- le pire cas est aux bornes de troncature
// serveur (watch_timeline.ACTIVITE_MAX / LIEU_MAX).
// ---------------------------------------------------------------------

// Un fixture qui pretend jouer le pire cas doit le PROUVER, pas seulement
// l'affirmer en commentaire : ces deux longueurs sont les bornes de
// troncature serveur (watch_timeline.ACTIVITE_MAX / LIEU_MAX), et un
// libelle plus court ne testerait rien.
(:test)
function testFixtureTimelineEstBienAuPireCas(logger) {
    Test.assertEqual(TL_ACTIVITE_MAX.length(), 30);
    Test.assertEqual(TL_LIEU_MAX.length(), 24);
    return true;
}

(:test)
function testDebordementTimelineHerosDepuisLeCacheNeDeborgePas(logger) {
    var base = Time.now().value();
    Cache.save({"t" => base, "rx" => base, "m" => "live", "al" => [],
                "nx" => [base + 2520, TL_ACTIVITE_MAX, TL_LIEU_MAX, 12]});
    var vue = new TimelineView();
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

(:test)
function testDebordementTimelineToutesLesSousPagesNeDeborgentPas(logger) {
    var base = Time.now().value();
    Cache.save({"t" => base, "rx" => base, "m" => "live", "al" => [],
                "nx" => null});
    var vue = new TimelineView();
    // Pire cas : le plafond serveur (watch_timeline.MAX_VIGNETTES = 12) avec
    // des libelles aux bornes de troncature et des comptes a deux chiffres.
    var pire = [];
    for (var i = 0; i < 12; i += 1) {
        pire.add([base + 600 * (i + 1), TL_ACTIVITE_MAX, TL_LIEU_MAX, 12]);
    }
    vue.onRecue(true, pire);
    var total = vue.nbSousPages();
    // 12 vignettes = 1 heros + 3 ecrans de quatre. Sans cette verification,
    // une regression ramenant le livret a une page ferait passer la boucle
    // sans rien couvrir.
    Test.assertEqual(total, 4);
    for (var i = 0; i < total; i += 1) {
        var dc = dcEnregistrement();
        vue.onUpdate(dc);
        verifierNeDebordePas(logger, dc, piedStandard(dc));
        vue.sousPageSuivante();
    }
    return true;
}

(:test)
function testDebordementTimelineRienDePrevuNeDeborgePas(logger) {
    Application.Storage.deleteValue(Cache.KEY);
    var vue = new TimelineView();
    vue.onRecue(true, []);
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

(:test)
function testDebordementTimelineIndisponibleNeDeborgePas(logger) {
    Application.Storage.deleteValue(Cache.KEY);
    var vue = new TimelineView();
    vue.onRecue(false, null);
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    return true;
}

// Contenu, pas seulement geometrie. Trois regressions possibles qu'aucun
// controle geometrique ne verrait : le delai remplace par une heure, le
// compte de factorisation perdu, et "rien de prevu" confondu avec une
// panne.

(:test)
function testTimelineAfficheUnDelaiPasSeulementUneHeure(logger) {
    // LA valeur de la page. Une heure seule ("08:00") oblige a un calcul
    // mental ; c'est le delai qui porte la decision.
    var base = Time.now().value();
    Cache.save({"t" => base, "rx" => base, "m" => "live", "al" => [],
                "nx" => [base + 2520, "Ouverture au public", "Controle", 0]});
    var vue = new TimelineView();
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    var rects = dc.rects();
    var trouve = false;
    for (var i = 0; i < rects.size(); i += 1) {
        var label = rects[i]["label"];
        if (label != null && label.equals("dans 42 min")) { trouve = true; }
    }
    Test.assert(trouve);
    return true;
}

(:test)
function testTimelineAfficheLeCompteDeFactorisation(logger) {
    // Une ligne qui en vaut huit doit le DIRE : c'est ce qui fait tenir 65
    // vignettes en 9 lignes sans mentir sur ce qui se passe.
    var base = Time.now().value();
    Cache.save({"t" => base, "rx" => base, "m" => "live", "al" => [],
                "nx" => [base + 2520, "Ouverture parkings", "CHINETTI", 8]});
    var vue = new TimelineView();
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    var rects = dc.rects();
    var trouve = false;
    for (var i = 0; i < rects.size(); i += 1) {
        var label = rects[i]["label"];
        if (label != null && label.find("(8)") != null) { trouve = true; }
    }
    Test.assert(trouve);
    return true;
}

(:test)
function testTimelineDistingueRienDePrevuEtIndisponible(logger) {
    // Deux etats que la page ne doit jamais confondre : rien n'est prevu
    // (normal l'essentiel de l'annee) et la source ne repond pas (incident).
    Application.Storage.deleteValue(Cache.KEY);
    var vide = new TimelineView();
    vide.onRecue(true, []);
    var dc1 = dcEnregistrement();
    vide.onUpdate(dc1);

    var casse = new TimelineView();
    casse.onRecue(false, null);
    var dc2 = dcEnregistrement();
    casse.onUpdate(dc2);

    Test.assert(tlContient(dc1, "rien de prevu"));
    Test.assert(!tlContient(dc1, "indisponible"));
    Test.assert(tlContient(dc2, "indisponible"));
    return true;
}

function tlContient(dc, texte) {
    var rects = dc.rects();
    for (var i = 0; i < rects.size(); i += 1) {
        var label = rects[i]["label"];
        if (label != null && label.find(texte) != null) {
            return true;
        }
    }
    return false;
}


// LE compte de factorisation ne doit JAMAIS etre tronque. Trouve a la
// sonde : "Ouverture des tribunes nord es (12)" sortait
// "Ouverture des tribunes nord es (1" -- un chiffre FAUX, qui annonce une
// tribune la ou il y en a douze. Un mot abrege se voit, un nombre ampute se
// lit comme une valeur. Invisible a tout controle geometrique : le texte
// tronque tient parfaitement dans l'ecran.
(:test)
function testTimelineNeTronqueJamaisLeCompte(logger) {
    var base = Time.now().value();
    Cache.save({"t" => base, "rx" => base, "m" => "live", "al" => [],
                "nx" => [base + 2520, TL_ACTIVITE_MAX, TL_LIEU_MAX, 12]});
    var vue = new TimelineView();
    var dc = dcEnregistrement();
    vue.onUpdate(dc);
    verifierNeDebordePas(logger, dc, piedStandard(dc));
    Test.assert(tlUnLabelFinitPar(dc, "(12)"));

    // Meme garantie sur les ecrans de liste, ou la corde est plus etroite
    // encore et la police plus petite.
    var pire = [];
    for (var i = 0; i < 4; i += 1) {
        pire.add([base + 600 * (i + 1), TL_ACTIVITE_MAX, TL_LIEU_MAX, 12]);
    }
    vue.onRecue(true, pire);
    vue.sousPageSuivante();
    var dc2 = dcEnregistrement();
    vue.onUpdate(dc2);
    verifierNeDebordePas(logger, dc2, piedStandard(dc2));
    Test.assert(tlUnLabelFinitPar(dc2, "(12)"));
    return true;
}

function tlUnLabelFinitPar(dc, fin) {
    var rects = dc.rects();
    for (var i = 0; i < rects.size(); i += 1) {
        var label = rects[i]["label"];
        if (label != null && label.length() >= fin.length()) {
            var queue = label.substring(label.length() - fin.length(),
                                        label.length());
            if (queue.equals(fin)) {
                return true;
            }
        }
    }
    return false;
}
