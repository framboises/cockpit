using Toybox.Application;

(:glance :background)
module Jeton {

    // Jeton compile a la construction par tools/build-avec-jeton.sh, qui
    // remplace la chaine vide ci-dessous dans une COPIE temporaire du
    // projet. Le depot ne contient donc jamais de secret.
    //
    // ⚠️ POURQUOI PAS UNE PROPERTY. Le jeton vivait dans
    // resources/properties/properties.xml, lu par
    // Application.Properties.getValue("token"). Mais les Properties, comme
    // Application.Storage, SURVIVENT AU SIDELOAD : une valeur ecrite un
    // jour dans les reglages -- par Garmin Express, ou par une version
    // anterieure de l'app -- ECRASE la valeur par defaut compilee, et c'est
    // elle qui part sur le reseau.
    //
    // Symptome observe : "jeton refuse" apres CINQ reconstructions, avec un
    // jeton pourtant present dans le binaire (verifie par `strings`) et
    // accepte par le serveur (HTTP 200 verifie). Le binaire portait le bon
    // jeton en DEFAUT, la montre en envoyait un autre.
    //
    // Une constante de code n'a pas cette faiblesse : rien ne peut la
    // surcharger, et ce qui est compile est ce qui part.
    const VALEUR = "";

    // Le jeton reellement utilise : la constante compilee d'abord, la
    // Property ensuite. L'ordre compte -- il donne le dernier mot a la
    // construction, pas a un reliquat de reglage.
    //
    // La Property reste lue en repli pour ne pas casser une installation
    // existante qui en dependrait, et parce qu'elle rend le simulateur
    // configurable sans rebuild.
    function valeur() {
        if (VALEUR != null && VALEUR.length() > 0) {
            return VALEUR;
        }
        return Application.Properties.getValue("token");
    }

    // Quatre premiers caracteres du jeton employe, pour l'affichage de
    // diagnostic. Assez pour distinguer deux jetons d'un coup d'oeil, trop
    // peu pour en reconstituer un -- il en fait 43.
    function empreinte() {
        var t = valeur();
        if (t == null || t.length() < 4) {
            return "----";
        }
        return t.substring(0, 4);
    }
}
