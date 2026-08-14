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

    // Le payload HTTP porte les alertes en dictionnaires ; le cache les stocke
    // en tableaux, moitie moins d'octets et d'objets a instancier au reveil de
    // la glance.
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
            "e" => (data != null) ? data["e"] : null,
            "er" => (data != null) ? data["er"] : null,
            "w" => (data != null) ? data["w"] : null,
            "wl" => (data != null && data["wl"] != null) ? data["wl"] : 0,
            "al" => al,
            "rx" => nowSec,
            "ok" => true
        };
    }

    function fetch(callback) {
        mCallback = callback;

        var mock = Application.Properties.getValue("mockData");
        if (mock != null && mock) {
            var scenario = Application.Properties.getValue("mockScenario");
            if (scenario == null) { scenario = 0; }
            callback.invoke(true, Mock.state(scenario, Time.now().value()));
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
            // Requete deja en vol et pas encore expiree : on ne double pas.
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

    function onReceive(responseCode as Lang.Number,
                        data as Lang.Dictionary or Lang.String
                            or PersistedContent.Iterator or Null) as Void {
        mInFlightSince = null;
        if (responseCode != 200 || data == null) {
            if (mCallback != null) {
                mCallback.invoke(false, null);
            }
            return;
        }
        var st = toCacheDict(data, Time.now().value());
        if (mCallback != null) {
            mCallback.invoke(true, st);
        }
    }
}
