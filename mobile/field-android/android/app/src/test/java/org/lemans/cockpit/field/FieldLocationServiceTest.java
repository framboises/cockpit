package org.lemans.cockpit.field;

import static org.junit.Assert.assertEquals;

import org.junit.Test;

public class FieldLocationServiceTest {

    // Statut fin_intervention (JS "low") sans exigence du serveur : plantage du service en 1.1
    @Test
    public void sansExigenceDuServeurLeProfilDuJsSApplique() {
        assertEquals("low", FieldLocationService.effectiveProfile(null, "low"));
        assertEquals("normal", FieldLocationService.effectiveProfile(null, "normal"));
        assertEquals("high", FieldLocationService.effectiveProfile(null, "high"));
    }

    @Test
    public void leProfilLePlusExigeantGagne() {
        assertEquals("high", FieldLocationService.effectiveProfile("high", "low"));
        assertEquals("high", FieldLocationService.effectiveProfile("high", "normal"));
        assertEquals("high", FieldLocationService.effectiveProfile("normal", "high"));
    }
}
