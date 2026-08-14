using Toybox.Application;

(:glance :background)
module Cache {

    // Version du schema stocke. Un cache d'une autre version est ignore
    // plutot que relu de travers apres une mise a jour de l'app.
    const SCHEMA = 1;
    const KEY = "st";

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
}
