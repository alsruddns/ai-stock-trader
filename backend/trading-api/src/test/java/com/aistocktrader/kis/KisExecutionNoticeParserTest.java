package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderSide;
import org.junit.jupiter.api.Test;

import javax.crypto.Cipher;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

class KisExecutionNoticeParserTest {
    private static final String KEY = "0123456789abcdef0123456789abcdef";
    private static final String IV = "0123456789abcdef";

    @Test
    void parsesOnlyDomesticConfirmedExecutionAndReturnsNoAccountIdentifiers() throws Exception {
        String[] fields = new String[26];
        java.util.Arrays.fill(fields, "");
        fields[0] = "HTS123";
        fields[1] = "1234567801";
        fields[2] = "90001234";
        fields[4] = "02";
        fields[8] = "005930";
        fields[9] = "2";
        fields[10] = "71000";
        fields[11] = "091501";
        fields[12] = "N";
        fields[13] = "2";

        var notice = KisExecutionNoticeParser.parseReal("H0STCNI0", encrypt(String.join("^", fields)),
                KEY, IV, "HTS123", "1234567801").orElseThrow();

        assertEquals(Market.KR, notice.market());
        assertEquals("90001234", notice.brokerOrderNumber());
        assertEquals("005930", notice.symbol());
        assertEquals(OrderSide.BUY, notice.side());
        assertEquals(2, notice.fillQuantity());
        assertEquals(0, new BigDecimal("71000").compareTo(notice.fillPrice()));
        assertEquals(java.time.LocalTime.of(9, 15, 1), notice.fillTime());
        assertTrue(!notice.toString().contains("1234567801"));
        assertTrue(!notice.toString().contains("HTS123"));
    }

    @Test
    void parsesOverseasExecutionAndRejectsWrongUserOrNonFillNotice() throws Exception {
        String[] fields = new String[25];
        java.util.Arrays.fill(fields, "");
        fields[0] = "HTS123";
        fields[1] = "1234567801";
        fields[2] = "90004567";
        fields[4] = "01";
        fields[7] = "AAPL";
        fields[8] = "1";
        fields[9] = "191.25";
        fields[10] = "093012";
        fields[11] = "N";
        fields[12] = "2";
        String encoded = encrypt(String.join("^", fields));

        var notice = KisExecutionNoticeParser.parseReal("H0GSCNI0", encoded, KEY, IV,
                "HTS123", "1234567801").orElseThrow();

        assertEquals(Market.US, notice.market());
        assertEquals(OrderSide.SELL, notice.side());
        assertEquals(0, new BigDecimal("191.25").compareTo(notice.fillPrice()));
        assertEquals(java.time.LocalTime.of(9, 30, 12), notice.fillTime());
        assertEquals(Optional.empty(), KisExecutionNoticeParser.parseReal("H0GSCNI0", encoded,
                KEY, IV, "OTHER", "1234567801"));
        assertEquals(Optional.empty(), KisExecutionNoticeParser.parseReal("H0GSCNI0", encoded,
                KEY, IV, "HTS123", "0000000000"));
        fields[12] = "1";
        assertEquals(Optional.empty(), KisExecutionNoticeParser.parseReal("H0GSCNI0",
                encrypt(String.join("^", fields)), KEY, IV, "HTS123", "1234567801"));
        fields[12] = "2";
        fields[10] = "badtime";
        assertEquals(Optional.empty(), KisExecutionNoticeParser.parseReal("H0GSCNI0",
                encrypt(String.join("^", fields)), KEY, IV, "HTS123", "1234567801"));
    }

    @Test
    void malformedOrUnsupportedFramesFailClosed() {
        assertEquals(Optional.empty(), KisExecutionNoticeParser.parseReal("H0STCNT0", "payload", KEY, IV, "HTS123", "1234567801"));
        assertEquals(Optional.empty(), KisExecutionNoticeParser.parseReal("H0STCNI0", "invalid", KEY, IV, "HTS123", "1234567801"));
        assertEquals(Optional.empty(), KisExecutionNoticeParser.parseReal("H0STCNI0", "payload", "short", IV, "HTS123", "1234567801"));
    }

    private static String encrypt(String value) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/CBC/PKCS5Padding");
        cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(KEY.getBytes(StandardCharsets.UTF_8), "AES"),
                new IvParameterSpec(IV.getBytes(StandardCharsets.UTF_8)));
        return Base64.getEncoder().encodeToString(cipher.doFinal(value.getBytes(StandardCharsets.UTF_8)));
    }
}
