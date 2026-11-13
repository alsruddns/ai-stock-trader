package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderSide;
import com.fasterxml.jackson.databind.JsonNode;

/** Rejects incomplete or contradictory broker quantities before they change a persisted order. */
final class KisBrokerOrderSnapshotValidator {
    private KisBrokerOrderSnapshotValidator() { }

    static boolean matchesOrderQuantity(KisOrderIntent intent, KisBrokerOrderSnapshot snapshot) {
        if (intent == null || snapshot == null || snapshot.filledQuantity() < 0
                || snapshot.remainingQuantity() < 0 || snapshot.filledQuantity() > intent.getQuantity()
                || snapshot.filledQuantity() + snapshot.remainingQuantity() > intent.getQuantity()
                || (snapshot.averageFillPrice() != null && snapshot.averageFillPrice().signum() < 0)
                || (snapshot.filledQuantity() == 0 && snapshot.averageFillPrice() != null
                        && snapshot.averageFillPrice().signum() > 0)
                || (snapshot.filledQuantity() > 0 && (snapshot.averageFillPrice() == null
                        || snapshot.averageFillPrice().signum() <= 0))
                || (snapshot.cancelled() && snapshot.rejected())) {
            return false;
        }

        if (snapshot.cancelled()) return snapshot.remainingQuantity() == 0
                && snapshot.filledQuantity() < intent.getQuantity();
        if (snapshot.rejected()) return snapshot.filledQuantity() == 0 && snapshot.remainingQuantity() == 0;
        return snapshot.filledQuantity() + snapshot.remainingQuantity() == intent.getQuantity();
    }

    static boolean matchesBrokerIdentity(JsonNode row, KisOrderIntent intent) {
        if (row == null || intent == null) return false;
        String symbol = firstText(row, "pdno", "PDNO", "ovrs_pdno", "OVRS_PDNO");
        String side = firstText(row, "sll_buy_dvsn_cd", "SLL_BUY_DVSN_CD", "sll_buy_dvsn", "SLL_BUY_DVSN");
        if (symbol.isBlank() || side.isBlank() || !intent.getSymbol().equalsIgnoreCase(symbol.trim())) return false;
        String expectedSideCode = intent.getSide() == OrderSide.BUY ? "02" : "01";
        if (!expectedSideCode.equals(side.trim()) && !intent.getSide().name().equalsIgnoreCase(side.trim())) return false;

        String expectedOrganization = intent.getBrokerOrganizationNumber();
        String brokerOrganization = firstText(row, "ord_gno_brno", "ORD_GNO_BRNO",
                "krx_fwdg_ord_orgno", "KRX_FWDG_ORD_ORGNO");
        if (intent.getMarket() == Market.KR) {
            return expectedOrganization != null && !expectedOrganization.isBlank()
                    && !brokerOrganization.isBlank()
                    && expectedOrganization.trim().equalsIgnoreCase(brokerOrganization.trim());
        }
        if (expectedOrganization == null || expectedOrganization.isBlank()) return true;
        if (brokerOrganization.isBlank()) return true;
        return expectedOrganization.trim().equalsIgnoreCase(brokerOrganization.trim());
    }

    private static String firstText(JsonNode row, String... fields) {
        for (String field : fields) {
            String value = row.path(field).asText("");
            if (!value.isBlank()) return value;
        }
        return "";
    }
}
