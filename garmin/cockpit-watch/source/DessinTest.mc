using Toybox.Test;
using Toybox.Graphics;
using Toybox.Time;
using Toybox.Application;

// Aucun test n'exercait le chemin de DESSIN : les tests portaient tous sur les
// fonctions pures. Un deref sur un champ nul dans drawMain -- typiquement `pk`
// ou `pkt` absents -- ne se serait vu qu'a l'ecran, apres sideload.
//
// Ces tests ne jugent pas l'esthetique (la mise en page a ete mesuree
// separement) : ils verifient que le rendu ne leve pas, dans chaque etat que
// la montre peut reellement atteindre.

function dcDeTest() {
    var bmp = Graphics.createBufferedBitmap({:width => 454, :height => 454});
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
