(:background)
module Mock {

    // Cinq scenarios pour eprouver l'affichage et les transitions sans
    // attendre un vrai pic : nominal, montee du WBGT, alerte critique,
    // donnee perimee, hors evenement.
    function state(scenario, nowSec) {
        if (scenario == 4) {
            // Hors evenement : plus de direct, mais le pic d'une edition
            // close. C'est l'etat de la montre l'essentiel de l'annee.
            return {"t" => null, "rx" => nowSec, "ok" => true, "m" => "past",
                    "n" => "LMC 26", "e" => null, "er" => null,
                    "pk" => 52409, "pkt" => 1783175368,
                    "w" => 24.2, "wl" => 0, "al" => []};
        }
        if (scenario == 1) {
            return {"t" => nowSec, "rx" => nowSec, "ok" => true, "m" => "live",
                    "n" => "24HM 26", "e" => 51200, "er" => 4100,
                    "pk" => 44100, "pkt" => nowSec - 3600,
                    "w" => 29.1, "wl" => 2, "al" => []};
        }
        if (scenario == 2) {
            return {"t" => nowSec, "rx" => nowSec, "ok" => true, "m" => "live",
                    "n" => "24HM 26", "e" => 62800, "er" => 5200,
                    "pk" => 50690, "pkt" => nowSec - 900,
                    "w" => 31.6, "wl" => 3,
                    "al" => [[3, "SOS tablette"], [2, "Vent 72 km/h"],
                             [1, "Ouverture imminente"]]};
        }
        if (scenario == 3) {
            // Recue a l'instant, mais la donnee date de 22 minutes.
            return {"t" => nowSec - 1320, "rx" => nowSec, "ok" => true,
                    "m" => "live", "n" => "24HM 26", "e" => 48213,
                    "er" => null, "pk" => 41000, "pkt" => nowSec - 7200,
                    "w" => 27.4, "wl" => 1, "al" => []};
        }
        return {"t" => nowSec, "rx" => nowSec, "ok" => true, "m" => "live",
                "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "pk" => 39800, "pkt" => nowSec - 5400,
                "w" => 27.4, "wl" => 1, "al" => [[1, "Ouverture imminente"]]};
    }

    // Liste d'editions du simulateur : les vraies valeurs relevees en base,
    // pour que la mise en page soit eprouvee sur des nombres a six chiffres
    // et des libelles de longueur reelle.
    function editions() {
        return [["LMC 26", 52409, 1783175368],
                ["24HA 26", 148919, 1781359710],
                ["GPF 26", 98593, 1778414696],
                ["24HM 26", 50690, 1776517509],
                ["24HA 25", 145571, 1749910253],
                ["GPF 25", 101194, 1746965110],
                ["24HM 25", 40077, 1745068064]];
    }
}
