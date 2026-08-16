(:background)
module Mock {

    // Sept scenarios pour eprouver l'affichage et les transitions sans
    // attendre un vrai pic : nominal, montee du WBGT, alerte critique,
    // donnee perimee, hors evenement (arret volontaire), source en panne
    // sur les quatre blocs, hors evenement (collecteur en panne).
    //
    // Ce dictionnaire imite le PAYLOAD BRUT du serveur, pas la forme du
    // cache : `al` porte donc des dictionnaires {"l"=>niveau, "m"=>libelle}
    // (cf. watch_state.select_alerts), pas les tuples compacts que le cache
    // stocke. C'est Api.fetch() qui fait passer ce dictionnaire par
    // toCacheDict/toPagesDict, exactement comme onReceive() le fait pour une
    // vraie reponse reseau -- la coupure noyau/pages ne doit se faire qu'a
    // cet unique endroit.
    function state(scenario, nowSec) {
        if (scenario == 4) {
            // Hors evenement, arret volontaire : plus de direct, mais le pic
            // d'une edition close. C'est l'etat de la montre l'essentiel de
            // l'annee -- trafic et meteo restent vivants, main courante et
            // frequentation non (aucun evenement a mesurer).
            return {"t" => null, "rx" => nowSec, "ok" => true, "m" => "past",
                    "mr" => "inactif", "n" => "LMC 26", "e" => null,
                    "er" => null, "p" => null,
                    "pk" => 52409, "pkt" => 1783175368,
                    "w" => 24.2, "wl" => 0, "al" => [],
                    "mc" => null,
                    "tr" => {"t" => nowSec, "vd" => 0, "ac" => 0, "z" => 0,
                             "r" => []},
                    "me" => {"t" => nowSec - 1800, "tc" => 18.5, "v" => 12.0,
                             "rf" => 20.0, "pl" => null, "pm" => null,
                             "cn" => null, "cl" => 0, "vg" => 0},
                    "st" => null};
        }
        if (scenario == 6) {
            // Hors evenement, mais le drapeau live-controle dit "actif" et
            // plus aucun releve n'arrive : le collecteur est en panne, pas
            // un arret volontaire. Sert a eprouver le pied "aucun releve" en
            // rouge, distinct du gris "live inactif" du scenario 4.
            return {"t" => null, "rx" => nowSec, "ok" => true, "m" => "past",
                    "mr" => "sans_releve", "n" => "LMC 26", "e" => null,
                    "er" => null, "p" => null,
                    "pk" => 52409, "pkt" => 1783175368,
                    "w" => 24.2, "wl" => 0, "al" => [],
                    "mc" => null,
                    "tr" => {"t" => nowSec, "vd" => 1, "ac" => 0, "z" => 2,
                             "r" => [["Porte Sud", "o", 6, 1]]},
                    "me" => {"t" => nowSec - 1800, "tc" => 18.5, "v" => 12.0,
                             "rf" => 20.0, "pl" => null, "pm" => null,
                             "cn" => null, "cl" => 0, "vg" => 0},
                    "st" => null};
        }
        if (scenario == 1) {
            // Montee du WBGT : quelques fiches en instance et un trafic en
            // vigilance -- la bande de voyants doit apparaitre, en jaune.
            return {"t" => nowSec, "rx" => nowSec, "ok" => true, "m" => "live",
                    "mr" => null, "n" => "24HM 26", "e" => 51200,
                    "er" => 4100, "p" => 47850,
                    "pk" => 44100, "pkt" => nowSec - 3600,
                    "w" => 29.1, "wl" => 2, "al" => [],
                    "mc" => {"t" => nowSec, "s" => [1, 4], "sc" => [0, 2],
                             "tq" => [1, 1], "f" => [0, 0], "o" => [0, 1]},
                    "tr" => {"t" => nowSec, "vd" => 1, "ac" => 0, "z" => 3,
                             "r" => [["Porte Sud", "o", 6, 1]]},
                    "me" => {"t" => nowSec - 900, "tc" => 29.1, "v" => 15.0,
                             "rf" => 28.0, "pl" => null, "pm" => null,
                             "cn" => null, "cl" => 1, "vg" => 1},
                    "st" => {"t" => nowSec - 600, "pj" => 44100,
                             "ph" => "13h20", "n1" => 40000}};
        }
        if (scenario == 2) {
            // Alerte critique : bande de voyants au pire niveau, en rouge.
            return {"t" => nowSec, "rx" => nowSec, "ok" => true, "m" => "live",
                    "mr" => null, "n" => "24HM 26", "e" => 62800,
                    "er" => 5200, "p" => 58400,
                    "pk" => 50690, "pkt" => nowSec - 900,
                    "w" => 31.6, "wl" => 3,
                    "al" => [{"l" => 3, "m" => "SOS tablette"},
                             {"l" => 2, "m" => "Vent 72 km/h"},
                             {"l" => 1, "m" => "Ouverture imminente"}],
                    "mc" => {"t" => nowSec, "s" => [3, 9], "sc" => [2, 5],
                             "tq" => [2, 3], "f" => [1, 2], "o" => [0, 2]},
                    "tr" => {"t" => nowSec, "vd" => 3, "ac" => 1, "z" => 6,
                             "r" => [["Porte Sud", "o", 22, 3],
                                     ["Panorama", "-", 14, 2]]},
                    "me" => {"t" => nowSec - 300, "tc" => 31.6, "v" => 25.0,
                             "rf" => 72.0, "pl" => 15, "pm" => 8.0,
                             "cn" => "Palpation renforcee", "cl" => 2,
                             "vg" => 2},
                    "st" => {"t" => nowSec - 120, "pj" => 50690,
                             "ph" => "15h05", "n1" => 44100},
                    // Seul scenario portant un guidage : les autres restent
                    // sans point, qui est l'etat normal. Porte Houx 5, coin
                    // nord-ouest du circuit.
                    "gs" => 3,
                    "gd" => {"lat" => 47.9503, "lon" => 0.2214,
                             "n" => "Porte Houx 5", "s" => 3,
                             "t" => nowSec - 60},
                    "nx" => [nowSec + 2520, "Ouverture au public",
                             "Controle", 0]};
        }
        if (scenario == 3) {
            // Recue a l'instant, mais la donnee date de 22 minutes. Calme
            // par ailleurs : aucun voyant.
            return {"t" => nowSec - 1320, "rx" => nowSec, "ok" => true,
                    "m" => "live", "mr" => null, "n" => "24HM 26", "e" => 48213,
                    "er" => null, "p" => 44980,
                    "pk" => 41000, "pkt" => nowSec - 7200,
                    "w" => 27.4, "wl" => 1, "al" => [],
                    "mc" => {"t" => nowSec - 1320, "s" => [0, 4], "sc" => [0, 1],
                             "tq" => [0, 0], "f" => [0, 0], "o" => [0, 0]},
                    "tr" => {"t" => nowSec - 1320, "vd" => 0, "ac" => 0,
                             "z" => 0, "r" => []},
                    "me" => {"t" => nowSec - 1800, "tc" => 27.4, "v" => 10.0,
                             "rf" => 18.0, "pl" => null, "pm" => null,
                             "cn" => null, "cl" => 0, "vg" => 0},
                    "st" => {"t" => nowSec - 1320, "pj" => 41000,
                             "ph" => "11h40", "n1" => 38000}};
        }
        if (scenario == 5) {
            // Source en panne sur les quatre blocs : le coeur (presents,
            // WBGT, alertes) reste intact, seules les pages operationnelles
            // sont indisponibles. La bande de voyants doit rester absente
            // sans lever, exactement comme si tout etait calme.
            return {"t" => nowSec, "rx" => nowSec, "ok" => true, "m" => "live",
                    "mr" => null, "n" => "24HM 26", "e" => 48213,
                    "er" => 3200, "p" => 44980,
                    "pk" => 39800, "pkt" => nowSec - 5400,
                    "w" => 27.4, "wl" => 1,
                    "al" => [{"l" => 1, "m" => "Ouverture imminente"}],
                    "mc" => null, "tr" => null, "me" => null, "st" => null};
        }
        // Scenario nominal (0 par defaut) : calme, aucun voyant.
        return {"t" => nowSec, "rx" => nowSec, "ok" => true, "m" => "live",
                "mr" => null, "n" => "24HM 26", "e" => 48213, "er" => 3200,
                "p" => 44980,
                "pk" => 39800, "pkt" => nowSec - 5400,
                "w" => 27.4, "wl" => 1,
                "al" => [{"l" => 1, "m" => "Ouverture imminente"}],
                "mc" => {"t" => nowSec, "s" => [0, 5], "sc" => [0, 2],
                         "tq" => [0, 1], "f" => [0, 0], "o" => [0, 1]},
                "tr" => {"t" => nowSec, "vd" => 0, "ac" => 0, "z" => 1,
                         "r" => [["Porte Sud", "o", 4, 0]]},
                "me" => {"t" => nowSec - 900, "tc" => 27.4, "v" => 10.0,
                         "rf" => 18.0, "pl" => null, "pm" => null,
                         "cn" => null, "cl" => 0, "vg" => 0},
                "st" => {"t" => nowSec - 900, "pj" => 39800, "ph" => "12h10",
                         "n1" => 38200}};
    }

    // Liste d'editions du simulateur : les vraies valeurs relevees en base,
    // pour que la mise en page soit eprouvee sur des nombres a six chiffres
    // et des libelles de longueur reelle.
    // Timeline simulee : le matin d'une journee de course, avec ses
    // ouvertures factorisees -- la forme reelle que rend
    // watch_timeline.prochaines.
    // `base` est passe par l'appelant, comme pour state(scenario, nowSec) :
    // ce module est annote (:background) et n'importe pas Toybox.Time.
    function timeline(base) {
        return [[base + 900, "Ouverture Centre accreditation", "Centre accreditation", 0],
                [base + 900, "Ouverture parkings", "CHINETTI, EXPO MOTOS, LMS", 8],
                [base + 4500, "Ouverture au public", "Controle", 0],
                [base + 4500, "Ouverture portes", "PORTE ANNEXE, PORTE CIK", 8],
                [base + 4500, "Ouverture tribunes", "SINGHER, SOMMER, DURAND", 5],
                [base + 12600, "Warm-up", "Piste", 0],
                [base + 21600, "Depart course", "Piste", 0]];
    }

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
