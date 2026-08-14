(:background)
module Mock {

    // Quatre scenarios pour eprouver l'affichage et les transitions sans
    // attendre un vrai pic : nominal, montee du WBGT, alerte critique,
    // donnee perimee.
    function state(scenario, nowSec) {
        if (scenario == 1) {
            return {"t" => nowSec, "rx" => nowSec, "ok" => true,
                    "n" => "24HM 26", "e" => 51200, "er" => 4100,
                    "w" => 29.1, "wl" => 2, "al" => []};
        }
        if (scenario == 2) {
            return {"t" => nowSec, "rx" => nowSec, "ok" => true,
                    "n" => "24HM 26", "e" => 62800, "er" => 5200,
                    "w" => 31.6, "wl" => 3,
                    "al" => [[3, "SOS tablette"], [2, "Vent 72 km/h"],
                             [1, "Ouverture imminente"]]};
        }
        if (scenario == 3) {
            // Recue a l'instant, mais la donnee date de 22 minutes.
            return {"t" => nowSec - 1320, "rx" => nowSec, "ok" => true,
                    "n" => "24HM 26", "e" => 48213, "er" => null,
                    "w" => 27.4, "wl" => 1, "al" => []};
        }
        return {"t" => nowSec, "rx" => nowSec, "ok" => true,
                "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "w" => 27.4, "wl" => 1, "al" => [[1, "Ouverture imminente"]]};
    }
}
