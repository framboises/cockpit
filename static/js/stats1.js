function updateStats() {
    // Ici vous mettrez la logique pour récupérer et afficher les données dynamiques.
    // Pour l'instant, nous affichons des placeholders et loguons l'action.
    console.log("Mise à jour des statistiques générales...");
    // Exemple : document.getElementById("current-present").querySelector(".card-number").textContent = "N/A";
}
updateStats();
// Actualisation toutes les 90 secondes (90000 millisecondes)
setInterval(updateStats, 90000);