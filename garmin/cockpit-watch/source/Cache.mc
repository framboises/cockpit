using Toybox.Application;

(:glance :background)
module Cache {

    // Version du schema stocke. Un cache d'une autre version est ignore
    // plutot que relu de travers apres une mise a jour de l'app.
    const SCHEMA = 1;
    const KEY = "st";

    // Seconde cle, lue par la seule app (les quatre blocs mc/tr/me/st du
    // payload). Cache coupe en deux expres : la glance et le service de
    // fond n'ont jamais besoin de deserialiser ce contenu, cf. Api.mc.
    const SCHEMA_PAGES = 1;
    const KEY_PAGES = "pg";

    function save(st) {
        st["v"] = SCHEMA;
        Application.Storage.setValue(KEY, st);
    }

    function load() {
        var st = Application.Storage.getValue(KEY);
        if (st == null || !(st instanceof Toybox.Lang.Dictionary)) {
            return null;
        }
        if (st["v"] != SCHEMA) {
            return null;
        }
        return st;
    }

    function clear() {
        Application.Storage.deleteValue(KEY);
    }

    function savePages(blocs) {
        blocs["v"] = SCHEMA_PAGES;
        Application.Storage.setValue(KEY_PAGES, blocs);
    }

    function loadPages() {
        var pg = Application.Storage.getValue(KEY_PAGES);
        if (pg == null || !(pg instanceof Toybox.Lang.Dictionary)) {
            return null;
        }
        if (pg["v"] != SCHEMA_PAGES) {
            return null;
        }
        return pg;
    }
}
