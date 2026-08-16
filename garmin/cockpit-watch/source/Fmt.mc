using Toybox.Lang;
using Toybox.Time;

(:glance :background)
module Fmt {

    const DASH = "--";

    // Espace insecable fine entre milliers : un compteur a six chiffres est
    // illisible d'un coup d'oeil sans separation.
    function count(n) {
        if (n == null) {
            return DASH;
        }
        var s = n.toString();
        var out = "";
        var c = 0;
        for (var i = s.length() - 1; i >= 0; i -= 1) {
            out = s.substring(i, i + 1) + out;
            c += 1;
            if (c % 3 == 0 && i > 0) {
                out = " " + out;
            }
        }
        return out;
    }

    function rate(n) {
        if (n == null) {
            return DASH;
        }
        return n.toString();
    }

    function wbgt(w) {
        if (w == null) {
            return DASH;
        }
        return w.format("%.1f");
    }

    function age(sec) {
        if (sec == null) {
            return DASH;
        }
        if (sec < 0) {
            sec = 0;
        }
        if (sec < 60) {
            return sec.toString() + " s";
        }
        if (sec < 3600) {
            return (sec / 60).toString() + " min";
        }
        // Au-dela de deux jours, les heures ne se lisent plus : un releve vieux
        // de 113 jours s'affichait "2725 h", chiffre qu'il faut diviser de tete
        // pour comprendre, et qui deborde de la largeur utile sur l'ecran rond.
        if (sec < 172800) {
            return (sec / 3600).toString() + " h";
        }
        return (sec / 86400).toString() + " j";
    }

    // Jour d'un instant donne en epoch UTC, rendu en HEURE LOCALE de la
    // montre -- donc Paris. Gregorian.info convertit lui-meme : lui passer un
    // Moment construit sur l'epoch suffit, il ne faut surtout pas decaler
    // l'epoch a la main avant, ce qui appliquerait le fuseau deux fois.
    function day(epochSec) {
        if (epochSec == null) {
            return DASH;
        }
        var info = Time.Gregorian.info(new Time.Moment(epochSec),
                                       Time.FORMAT_MEDIUM);
        return info.day_of_week + " " + info.day.toString() + " " + info.month;
    }

    // Compte a rebours jusqu'a un instant futur : "dans 42 min", "dans 3 h",
    // "maintenant". C'est LA valeur de la page Timeline -- une heure seule
    // ("08:00") oblige a un calcul mental, un delai se lit d'un coup.
    //
    // Calcule au POIGNET a partir d'un epoch, jamais formate cote serveur :
    // un releve peut avoir jusqu'a trois minutes de retard, et un "dans 42
    // min" fige a l'emission serait faux a l'arrivee.
    //
    // Le passe rend "maintenant" plutot qu'un delai negatif : entre le
    // dernier releve et l'affichage, une vignette peut avoir franchi son
    // heure sans que le serveur ait eu le temps de la retirer. Annoncer
    // "dans -2 min" serait absurde ; "maintenant" est exactement juste.
    function delai(epochSec, nowSec) {
        if (epochSec == null || nowSec == null) {
            return DASH;
        }
        var reste = epochSec - nowSec;
        if (reste < 60) {
            return "maintenant";
        }
        if (reste < 3600) {
            return "dans " + (reste / 60).toString() + " min";
        }
        // Au-dela d'une heure, les minutes n'ajoutent rien a la decision :
        // on retient "dans 3 h", pas "dans 3 h 12".
        return "dans " + (reste / 3600).toString() + " h";
    }

    // Heure d'un instant, meme convention locale. Le pic d'affluence se lit
    // autant a son heure qu'a sa date : "sam. 18 avr. 15h05" dit une chose
    // qu'une date seule ne dit pas.
    function hour(epochSec) {
        if (epochSec == null) {
            return DASH;
        }
        var info = Time.Gregorian.info(new Time.Moment(epochSec),
                                       Time.FORMAT_SHORT);
        return info.hour.format("%02d") + "h" + info.min.format("%02d");
    }
}
