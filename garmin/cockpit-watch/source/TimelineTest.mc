using Toybox.Test;
using Toybox.Application;
using Toybox.Time;

// Tests en VALEUR. Un livret qui boucle mal, un heros pris a la mauvaise
// source, ou un compte de factorisation perdu ne levent aucune exception --
// ils affichent simplement autre chose que ce qu'il faut.

// Vignettes de test : [epoch, activite, lieu, compte de factorisation].
function tlVignettes(base) {
    return [[base + 900, "Ouverture Centre accreditation", "Accreditation", 0],
            [base + 900, "Ouverture parkings", "CHINETTI, EXPO", 8],
            [base + 4500, "Ouverture au public", "Controle", 0],
            [base + 4500, "Ouverture portes", "PORTE ANNEXE", 8],
            [base + 12600, "Warm-up", "Piste", 0],
            [base + 21600, "Depart course", "Piste", 0]];
}

function tlVueChargee() {
    var vue = new TimelineView();
    vue.onRecue(true, tlVignettes(Time.now().value()));
    return vue;
}

(:test)
function testTimelineListeVideAuDepart(logger) {
    Application.Storage.deleteValue(Cache.KEY);
    var vue = new TimelineView();
    Test.assertEqual(vue.vignettes().size(), 0);
    Test.assertEqual(vue.nbSousPages(), 1);
    Test.assertEqual(vue.sousPage(), 0);
    return true;
}

(:test)
function testTimelineUnEcranParTrancheDeQuatre(logger) {
    // 6 vignettes = 1 heros + 2 ecrans (quatre puis deux).
    Test.assertEqual(tlVueChargee().nbSousPages(), 3);
    return true;
}

(:test)
function testTimelineArrondiVersLeHaut(logger) {
    // 5 vignettes = 1 heros + 2 ecrans : la cinquieme ne doit pas
    // disparaitre parce que la division tombe juste au-dessous.
    var vue = new TimelineView();
    var base = Time.now().value();
    var cinq = [];
    for (var i = 0; i < 5; i += 1) {
        cinq.add([base + 600 * (i + 1), "Acte " + i.toString(), "La", 0]);
    }
    vue.onRecue(true, cinq);
    Test.assertEqual(vue.nbSousPages(), 3);
    return true;
}

(:test)
function testTimelineStartBoucleSurLeHeros(logger) {
    var vue = tlVueChargee();
    Test.assertEqual(vue.sousPage(), 0);
    vue.sousPageSuivante();
    Test.assertEqual(vue.sousPage(), 1);
    vue.sousPageSuivante();
    Test.assertEqual(vue.sousPage(), 2);
    vue.sousPageSuivante();
    Test.assertEqual(vue.sousPage(), 0);
    return true;
}

(:test)
function testTimelineIndexBorneQuandLaListeRaccourcit(logger) {
    // LA regression a tenir : la liste raccourcit toute seule au fil des
    // heures (une vignette passee disparait du prochain releve). Sans
    // bornage, l'utilisateur resterait bloque sur un ecran vide.
    var vue = tlVueChargee();
    vue.sousPageSuivante();
    vue.sousPageSuivante();
    Test.assertEqual(vue.sousPage(), 2);
    vue.onRecue(true, [[Time.now().value() + 600, "Seul", "La", 0]]);
    Test.assertEqual(vue.nbSousPages(), 2);
    Test.assertEqual(vue.sousPage(), 1);
    return true;
}

(:test)
function testTimelineRemiseAZero(logger) {
    var vue = tlVueChargee();
    vue.sousPageSuivante();
    vue.remiseAZero();
    Test.assertEqual(vue.sousPage(), 0);
    return true;
}

// --- Le heros : deux sources, un seul chemin de rendu ------------------

(:test)
function testHerosVientDuCacheAvantLaListe(logger) {
    // C'est ce qui fait qu'on n'attend jamais devant un ecran vide : `nx`
    // arrive dans le payload normal, donc dans le cache, donc la page
    // affiche quelque chose des son ouverture -- meme hors de portee du
    // telephone.
    var base = Time.now().value();
    Cache.save({"t" => base, "rx" => base, "m" => "live", "al" => [],
                "nx" => [base + 2520, "Ouverture au public", "Controle", 0]});
    var vue = new TimelineView();
    var h = vue.heros();
    Test.assert(h != null);
    Test.assertEqual(h[1], "Ouverture au public");
    return true;
}

(:test)
function testHerosPasseALaListeDesQuElleArrive(logger) {
    // La liste est plus FRAICHE que le noyau : elle est demandee a
    // l'ouverture de la page, quand le cache peut dater de trois minutes.
    var base = Time.now().value();
    Cache.save({"t" => base, "rx" => base, "m" => "live", "al" => [],
                "nx" => [base + 2520, "Vieille vignette", "Ailleurs", 0]});
    var vue = new TimelineView();
    Test.assertEqual(vue.heros()[1], "Vieille vignette");
    vue.onRecue(true, tlVignettes(base));
    Test.assertEqual(vue.heros()[1], "Ouverture Centre accreditation");
    return true;
}

(:test)
function testHerosNulSansCacheNiListe(logger) {
    Application.Storage.deleteValue(Cache.KEY);
    Test.assert(new TimelineView().heros() == null);
    return true;
}

(:test)
function testHerosNulQuandLeCacheNaPasDeProchaine(logger) {
    // Hors evenement : le payload porte `nx` a null. Ce n'est pas une
    // panne, c'est l'etat normal l'essentiel de l'annee.
    var base = Time.now().value();
    Cache.save({"t" => base, "rx" => base, "m" => "past", "al" => [],
                "nx" => null});
    Test.assert(new TimelineView().heros() == null);
    return true;
}

// --- Libelle et compte de factorisation --------------------------------

(:test)
function testLibelleSansFactorisation(logger) {
    var vue = new TimelineView();
    Test.assertEqual(vue.libelle([0, "Ouverture au public", "Controle", 0]),
                     "Ouverture au public");
    return true;
}

(:test)
function testLibellePorteLeCompteQuandPlusieurs(logger) {
    // Le compte arrive SEPARE du libelle cote serveur : la vue le remet, et
    // c'est ce qui dit qu'une ligne en vaut huit.
    var vue = new TimelineView();
    Test.assertEqual(vue.libelle([0, "Ouverture parkings", "X", 8]),
                     "Ouverture parkings (8)");
    return true;
}

(:test)
function testLibelleIgnoreUnCompteDeUn(logger) {
    // "(1)" n'apprendrait rien et mangerait la corde.
    var vue = new TimelineView();
    Test.assertEqual(vue.libelle([0, "Ouverture Parking CHINETTI", "X", 1]),
                     "Ouverture Parking CHINETTI");
    return true;
}

(:test)
function testLibelleToleereUneVignetteCourte(logger) {
    // Un cache ecrit par une version anterieure du transport peut porter
    // une vignette a trois elements : la vue ne doit pas lever dessus.
    var vue = new TimelineView();
    Test.assertEqual(vue.libelle([0, "Acte", "La"]), "Acte");
    Test.assertEqual(vue.libelle(null), "");
    return true;
}
