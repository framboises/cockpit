using Toybox.Communications;
using Toybox.Application;
using Toybox.Time;
using Toybox.Lang;
using Toybox.PersistedContent;

(:background)
module Api {

    // Une seule requete en vol a la fois : sur un reseau lent, le timer peut
    // redemander avant la reponse precedente, et la plus ancienne ecraserait
    // alors la plus recente dans le cache. C'est aussi du trafic BLE gaspille,
    // ce qui compte sur une cible de 24 h d'autonomie.
    //
    // La garde est bornee dans le temps : si un callback ne revient jamais, on
    // ne doit pas cesser definitivement de se rafraichir.
    const IN_FLIGHT_TIMEOUT_S = 30;

    // `hidden` est refuse a l'echelle module par le compilateur : ces variables
    // sont donc visibles de partout. Elles restent l'etat interne d'Api, aucun
    // autre fichier ne doit y toucher.
    var mCallback = null;
    var mInFlightSince = null;

    // Callback SEPARE pour /editions. mCallback est unique : si les deux
    // requetes partageaient le meme, une reponse d'editions arrivee pendant
    // qu'une requete d'etat est en vol serait remise au mauvais destinataire
    // -- la vue principale recevrait une liste d'editions a la place de son
    // etat. La garde mInFlightSince, elle, reste PARTAGEE : une seule requete
    // sur le lien BLE a la fois, c'est tout son objet.
    var mEditionsCallback = null;
    var mTimelineCallback = null;

    // Code de la derniere reponse /state, conserve en Storage pour survivre
    // au redemarrage de l'app ET etre visible depuis le service de fond.
    //
    // Il etait JETE : `onReceive` testait `responseCode != 200` puis
    // rendait `false` sans dire pourquoi. Un jeton revoque (401), un
    // depassement de quota (429), un serveur eteint (-2/-104 selon la
    // couche) et un Bluetooth coupe produisaient tous le meme silence --
    // et la montre affichait un cache vieux de trois heures sans que rien
    // ne permette de choisir entre << rapproche le telephone >> et
    // << ton jeton est mort >>.
    const KEY_ERR = "lerr";

    // Codes internes, hors de la plage HTTP et hors des codes negatifs de
    // Communications : la montre n'a meme PAS ESSAYE d'appeler le serveur.
    // Distinguer ce cas d'un refus est ce qui evite de chercher un probleme
    // de jeton la ou il n'y a pas de jeton du tout.
    const ERR_SANS_JETON = 1001;

    // Le payload HTTP porte les alertes en dictionnaires ; le cache les stocke
    // en tableaux, moitie moins d'octets et d'objets a instancier au reveil de
    // la glance.
    // Code de la derniere reponse en echec, ou null si le dernier echange a
    // reussi. Publique pour rester testable en VALEUR et lisible par les
    // vues.
    function derniereErreur() {
        return Application.Storage.getValue(KEY_ERR);
    }

    // Efface l'erreur memorisee. Appelee au demarrage de l'app
    // (CockpitApp.onStart) : Application.Storage survit au sideload, donc
    // sans cet oubli une erreur d'une installation precedente survivrait a
    // la reinstallation censee la corriger.
    function oublierErreur() {
        Application.Storage.deleteValue(KEY_ERR);
    }

    // Mot correspondant, ou null. Le CODE seul ("-104") ne dit rien a qui
    // regarde sa montre ; le mot dit quoi faire.
    //
    // Les codes negatifs sont ceux de Communications (BLE_HOST_TIMEOUT,
    // BLE_CONNECTION_UNAVAILABLE, etc.) : ils veulent tous dire la meme
    // chose a l'usage -- le telephone n'est pas joignable. Les positifs
    // viennent du serveur et se distinguent, eux.
    function motErreur(code) {
        if (code == null) { return null; }
        // Le cas le plus trompeur, et le plus frequent apres un sideload :
        // l'app n'a pas de jeton compile. Elle n'appelle donc jamais le
        // serveur -- dire "jeton refuse" enverrait chercher une revocation
        // qui n'existe pas.
        if (code == ERR_SANS_JETON) { return "jeton absent"; }
        if (code == 401) { return "jeton refuse"; }
        if (code == 429) { return "trop de requetes"; }
        if (code >= 500) { return "serveur en panne"; }
        if (code > 0) { return "erreur " + code.toString(); }
        return "telephone injoignable";
    }

    function toCacheDict(data, nowSec) {
        var al = [];
        var brut = (data != null) ? data["al"] : null;
        if (brut != null) {
            for (var i = 0; i < brut.size(); i += 1) {
                al.add([brut[i]["l"], brut[i]["m"]]);
            }
        }
        return {
            "t" => (data != null) ? data["t"] : null,
            "n" => (data != null) ? data["n"] : null,
            // Cette fonction recopie champ par champ : tout champ ajoute au
            // payload serveur et oublie ici serait perdu en SILENCE, sans
            // erreur ni trace. C'est le cas de m/pk/pkt.
            "m" => (data != null && data["m"] != null) ? data["m"] : "live",
            "mr" => (data != null) ? data["mr"] : null,
            "e" => (data != null) ? data["e"] : null,
            "er" => (data != null) ? data["er"] : null,
            "p" => (data != null) ? data["p"] : null,
            "pk" => (data != null) ? data["pk"] : null,
            "pkt" => (data != null) ? data["pkt"] : null,
            "w" => (data != null) ? data["w"] : null,
            "wl" => (data != null && data["wl"] != null) ? data["wl"] : 0,
            // Compteur de sequence du guidage, SEUL dans le noyau (le point
            // complet va dans les pages, ci-dessous). Le service de fond ne
            // lit que le noyau : sans ce scalaire, un point envoye pendant
            // que l'app est fermee n'aurait fait vibrer personne.
            "gs" => (data != null) ? data["gs"] : null,
            // La PROCHAINE vignette de timeline, seule : [epoch, activite,
            // lieu, compte]. La liste complete vit sur /timeline, requetee
            // paresseusement. Elle est dans le NOYAU et non dans les pages
            // parce qu'elle tient en 70 octets et qu'elle sert au coup
            // d'oeil -- c'est aussi ce qui permettra un jour de la poser sur
            // la glance sans requete supplementaire.
            "nx" => (data != null) ? data["nx"] : null,
            "al" => al,
            "rx" => nowSec,
            "ok" => true
        };
    }

    // Les quatre blocs vont dans une SECONDE cle Storage, lue par la seule
    // app. Les mettre dans le noyau ferait deserialiser du trafic a la glance
    // a chaque affichage, sur un budget de 64 Ko dont elle utilise deja 11 %.
    function toPagesDict(data) {
        if (data == null) {
            return null;
        }
        return {
            "mc" => data["mc"],
            "tr" => data["tr"],
            "me" => data["me"],
            "st" => data["st"],
            // Le point de guidage complet : lu par la seule page Guidage,
            // donc range avec les blocs de pages et jamais dans le noyau
            // que deserialise la glance a chaque affichage.
            "gd" => data["gd"]
        };
    }

    // Les editions arrivent en dictionnaires ; on les compacte en tableaux
    // comme les alertes, meme raison : moins d'octets en Storage et moins
    // d'objets a instancier.
    function toEditionsList(data) {
        var out = [];
        var brut = (data != null) ? data["ed"] : null;
        if (brut == null) {
            return out;
        }
        for (var i = 0; i < brut.size(); i += 1) {
            var e = brut[i];
            out.add([e["n"], e["pk"], e["pkt"]]);
        }
        return out;
    }

    // La liste arrive deja compactee en tableaux cote serveur
    // (watch_timeline._compacter) : rien a reformer ici, seulement a
    // extraire et a se proteger d'une reponse malformee.
    function toTimelineList(data) {
        var brut = (data != null) ? data["tl"] : null;
        if (brut == null) {
            return [];
        }
        return brut;
    }

    function fetch(callback) {
        mCallback = callback;

        var mock = Application.Properties.getValue("mockData");
        if (mock != null && mock) {
            var scenario = Application.Properties.getValue("mockScenario");
            if (scenario == null) { scenario = 0; }
            // Symetrique du chemin reseau (onReceive) : Mock.state() imite le
            // payload COMPLET du serveur, donc la coupure noyau/pages doit se
            // faire ici de la meme facon, avec les memes fonctions. Sans ca,
            // Cache.save() dans onFetched stockerait les quatre blocs
            // verbatim dans le noyau -- exactement la forme que la coupure en
            // deux cles Storage cherche a empecher, et que la glance ne
            // produit jamais en production.
            var etat = Mock.state(scenario, Time.now().value());
            Cache.savePages(toPagesDict(etat));
            callback.invoke(true, toCacheDict(etat, Time.now().value()));
            return;
        }

        var host = Application.Properties.getValue("host");
        var token = Application.Properties.getValue("token");
        if (host == null || host.length() == 0
            || token == null || token.length() == 0) {
            // AUCUNE REQUETE N'EST ENVOYEE ICI, et c'est tout le piege :
            // sans ce marquage, KEY_ERR gardait la valeur d'un essai
            // PRECEDENT. Un 401 vieux d'une semaine restait donc affiche
            // alors que la montre ne parlait meme plus au serveur -- on
            // pouvait reconstruire l'app dix fois sans que le message
            // change, puisque rien ne le reecrivait ni ne l'effacait.
            //
            // Constate a l'usage : trois reconstructions successives, le
            // meme "jeton refuse" a l'ecran, et un jeton pourtant accepte
            // en curl. Le vrai etat n'etait pas << refuse >> mais
            // << jamais configure >>.
            Application.Storage.setValue(KEY_ERR, ERR_SANS_JETON);
            callback.invoke(false, null);
            return;
        }

        var maintenant = Time.now().value();
        if (mInFlightSince != null
            && (maintenant - mInFlightSince) < IN_FLIGHT_TIMEOUT_S) {
            // Requete deja en vol et pas encore expiree : on ne double pas.
            // Pas de marquage : ce n'est pas une panne, c'est une garde.
            callback.invoke(false, null);
            return;
        }
        mInFlightSince = maintenant;

        // HTTPS obligatoire : makeWebRequest refuse un certificat auto-signe.
        var url = "https://" + host + "/api/v1/watch/state";
        Communications.makeWebRequest(
            url,
            {},
            {
                :method => Communications.HTTP_REQUEST_METHOD_GET,
                :headers => {
                    "Authorization" => "Bearer " + token
                },
                :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON
            },
            new Lang.Method(Api, :onReceive)
        );
    }

    function fetchEditions(callback) {
        var mock = Application.Properties.getValue("mockData");
        if (mock != null && mock) {
            callback.invoke(true, Mock.editions());
            return;
        }

        var host = Application.Properties.getValue("host");
        var token = Application.Properties.getValue("token");
        if (host == null || host.length() == 0
            || token == null || token.length() == 0) {
            callback.invoke(false, null);
            return;
        }

        var maintenant = Time.now().value();
        if (mInFlightSince != null
            && (maintenant - mInFlightSince) < IN_FLIGHT_TIMEOUT_S) {
            callback.invoke(false, null);
            return;
        }
        mInFlightSince = maintenant;
        // Arme apres la garde, pas avant : un appel refuse ne doit pas
        // remplacer le destinataire d'une reponse deja en vol.
        mEditionsCallback = callback;

        Communications.makeWebRequest(
            "https://" + host + "/api/v1/watch/editions",
            {},
            {
                :method => Communications.HTTP_REQUEST_METHOD_GET,
                :headers => {
                    "Authorization" => "Bearer " + token
                },
                :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON
            },
            new Lang.Method(Api, :onReceiveEditions)
        );
    }

    function fetchTimeline(callback) {
        var mock = Application.Properties.getValue("mockData");
        if (mock != null && mock) {
            callback.invoke(true, Mock.timeline(Time.now().value()));
            return;
        }

        var host = Application.Properties.getValue("host");
        var token = Application.Properties.getValue("token");
        if (host == null || host.length() == 0
            || token == null || token.length() == 0) {
            callback.invoke(false, null);
            return;
        }

        var maintenant = Time.now().value();
        if (mInFlightSince != null
            && (maintenant - mInFlightSince) < IN_FLIGHT_TIMEOUT_S) {
            callback.invoke(false, null);
            return;
        }
        mInFlightSince = maintenant;
        // Arme apres la garde, pas avant : un appel refuse ne doit pas
        // remplacer le destinataire d'une reponse deja en vol.
        mTimelineCallback = callback;

        Communications.makeWebRequest(
            "https://" + host + "/api/v1/watch/timeline",
            {},
            {
                :method => Communications.HTTP_REQUEST_METHOD_GET,
                :headers => {
                    "Authorization" => "Bearer " + token
                },
                :responseType => Communications.HTTP_RESPONSE_CONTENT_TYPE_JSON
            },
            new Lang.Method(Api, :onReceiveTimeline)
        );
    }

    function onReceiveTimeline(responseCode as Lang.Number,
                        data as Lang.Dictionary or Lang.String
                            or PersistedContent.Iterator or Null) as Void {
        mInFlightSince = null;
        if (responseCode != 200 || data == null) {
            if (mTimelineCallback != null) {
                mTimelineCallback.invoke(false, null);
            }
            return;
        }
        if (mTimelineCallback != null) {
            mTimelineCallback.invoke(true, toTimelineList(data));
        }
    }

    function onReceiveEditions(responseCode as Lang.Number,
                        data as Lang.Dictionary or Lang.String
                            or PersistedContent.Iterator or Null) as Void {
        mInFlightSince = null;
        if (responseCode != 200 || data == null) {
            if (mEditionsCallback != null) {
                mEditionsCallback.invoke(false, null);
            }
            return;
        }
        if (mEditionsCallback != null) {
            mEditionsCallback.invoke(true, toEditionsList(data));
        }
    }

    function onReceive(responseCode as Lang.Number,
                        data as Lang.Dictionary or Lang.String
                            or PersistedContent.Iterator or Null) as Void {
        mInFlightSince = null;
        if (responseCode != 200 || data == null) {
            // Conserve la CAUSE, pas seulement l'echec : c'est elle qui dit
            // quel geste faire.
            Application.Storage.setValue(KEY_ERR, responseCode);
            if (mCallback != null) {
                mCallback.invoke(false, null);
            }
            return;
        }
        // Un succes efface l'erreur : sans ca, un 401 vieux d'une semaine
        // resterait affiche alors que tout remarche.
        Application.Storage.deleteValue(KEY_ERR);
        var st = toCacheDict(data, Time.now().value());
        Cache.savePages(toPagesDict(data));
        if (mCallback != null) {
            mCallback.invoke(true, st);
        }
    }
}
