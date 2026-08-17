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

    // Temps de parcours : "4m 20s", "45s". Port EXACT de formatTime
    // (static/js/traffic.js:11), le bloc << Temps d'acces >> du cockpit.
    //
    // C'est la reference retenue pour la liste d'axes de la montre, et non
    // le mur circulation : "24'" seul est ambigu -- minutes ou secondes ? --
    // sur des trajets qui vont de quelques dizaines de secondes a une
    // demi-heure. Le format du cockpit ne laisse aucun doute.
    //
    // Les secondes sont omises quand elles valent zero ("4m", pas "4m 00s") :
    // c'est ce que fait le cockpit, et la corde d'une ligne d'axe est
    // comptee.
    function duree(sec) {
        if (sec == null) {
            return DASH;
        }
        var s = sec < 0 ? 0 : sec;
        var m = s / 60;
        var r = s % 60;
        if (m > 0) {
            if (r == 0) {
                return m.toString() + "m";
            }
            return m.toString() + "m " + (r < 10 ? "0" : "") + r.toString() + "s";
        }
        return s.toString() + "s";
    }

    // Retard par rapport a l'historique : "+0s", "+45s", "+1m 30s". Port
    // EXACT de formatDelay (static/js/traffic.js:20).
    //
    // TOUJOURS affiche, "+0s" compris -- c'est ce que fait le cockpit, et
    // c'est ce qui distingue un axe FLUIDE (retard connu, nul) d'un axe
    // dont on ignore le retard. Masquer le zero les confondrait.
    function retard(sec) {
        if (sec == null) {
            return DASH;
        }
        var s = sec < 0 ? 0 : sec;
        if (s == 0) {
            return "+0s";
        }
        var m = s / 60;
        var r = s % 60;
        if (m > 0 && r > 0) {
            return "+" + m.toString() + "m " + r.toString() + "s";
        }
        if (m > 0) {
            return "+" + m.toString() + "m";
        }
        return "+" + r.toString() + "s";
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
