using Toybox.Test;
using Toybox.Graphics;
using Toybox.Time;

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
    var vue = new CockpitView();
    Cache.save({"t" => Time.now().value(), "rx" => Time.now().value(),
                "m" => "live", "n" => "24HM 26", "e" => 48213, "er" => 3200,
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
    var vue = new CockpitView();
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
