package com.aistocktrader.kis;

/** Read-only broker evidence for a domestic PAPER order that KIS reports as cancelable. */
public record KisPaperCancelableOrder(
        String symbol,
        String brokerOrderNumber,
        String brokerOrganizationNumber,
        int cancelableQuantity) {
}
