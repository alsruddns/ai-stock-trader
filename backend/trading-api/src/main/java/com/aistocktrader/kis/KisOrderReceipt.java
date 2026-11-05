package com.aistocktrader.kis;

import java.time.Instant;

public record KisOrderReceipt(String environment, String market, String symbol, String side,
                              String brokerOrderNumber, String brokerOrganizationNumber,
                              String responseCode, Instant acceptedAt) { }
