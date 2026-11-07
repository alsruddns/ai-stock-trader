package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import com.aistocktrader.order.OrderSide;

import javax.crypto.Cipher;
import javax.crypto.spec.IvParameterSpec;
import javax.crypto.spec.SecretKeySpec;
import java.math.BigDecimal;
import java.nio.charset.StandardCharsets;
import java.time.LocalTime;
import java.util.Base64;
import java.util.Optional;
import java.util.regex.Pattern;

/** Parses only confirmed REAL execution notices; account identifiers are never returned or logged. */
public final class KisExecutionNoticeParser {
    private static final Pattern ORDER_NUMBER = Pattern.compile("[A-Za-z0-9_-]{1,32}");
    private static final Pattern KR_SYMBOL = Pattern.compile("\\d{6}");
    private static final Pattern US_SYMBOL = Pattern.compile("[A-Za-z][A-Za-z0-9.\\-]{0,15}");

    private KisExecutionNoticeParser() { }

    public static Optional<ExecutionNotice> parseReal(String transactionId, String encryptedPayload,
                                                       String aesKey, String aesIv, String expectedHtsId,
                                                       String expectedAccountNumber) {
        if (transactionId == null || encryptedPayload == null || aesKey == null || aesIv == null
                || expectedHtsId == null || expectedHtsId.isBlank() || expectedAccountNumber == null
                || !expectedAccountNumber.matches("\\d{10}")) return Optional.empty();
        try {
            Market market;
            if ("H0STCNI0".equals(transactionId)) market = Market.KR;
            else if ("H0GSCNI0".equals(transactionId)) market = Market.US;
            else return Optional.empty();

            String decoded = decrypt(encryptedPayload, aesKey, aesIv);
            String[] fields = decoded.split("\\^", -1);
            int fillFlagIndex = market == Market.KR ? 13 : 12;
            int minFieldCount = market == Market.KR ? 26 : 25;
            if (fields.length < minFieldCount || !expectedHtsId.equals(fields[0])
                    || !expectedAccountNumber.equals(fields[1])
                    || !"2".equals(fields[fillFlagIndex])) return Optional.empty();

            int orderIndex = 2;
            int sideIndex = 4;
            int timeIndex = market == Market.KR ? 11 : 10;
            int symbolIndex = market == Market.KR ? 8 : 7;
            int quantityIndex = market == Market.KR ? 9 : 8;
            int priceIndex = market == Market.KR ? 10 : 9;
            int rejectIndex = market == Market.KR ? 12 : 11;
            if ("Y".equalsIgnoreCase(fields[rejectIndex])) return Optional.empty();

            String brokerOrderNumber = fields[orderIndex];
            String symbol = fields[symbolIndex].trim().toUpperCase(java.util.Locale.ROOT);
            Pattern symbolPattern = market == Market.KR ? KR_SYMBOL : US_SYMBOL;
            if (!ORDER_NUMBER.matcher(brokerOrderNumber).matches() || !symbolPattern.matcher(symbol).matches()) {
                return Optional.empty();
            }
            OrderSide side = switch (fields[sideIndex]) {
                case "01" -> OrderSide.SELL;
                case "02" -> OrderSide.BUY;
                default -> null;
            };
            int quantity = Integer.parseInt(fields[quantityIndex]);
            BigDecimal price = new BigDecimal(fields[priceIndex]);
            LocalTime fillTime = LocalTime.parse(fields[timeIndex], java.time.format.DateTimeFormatter.ofPattern("HHmmss"));
            if (side == null || quantity <= 0 || price.signum() <= 0) return Optional.empty();
            return Optional.of(new ExecutionNotice(market, brokerOrderNumber, symbol, side, quantity, price, fillTime));
        } catch (Exception ignored) {
            return Optional.empty();
        }
    }

    private static String decrypt(String payload, String key, String iv) throws Exception {
        byte[] keyBytes = key.getBytes(StandardCharsets.UTF_8);
        byte[] ivBytes = iv.getBytes(StandardCharsets.UTF_8);
        if (keyBytes.length != 32 || ivBytes.length != 16 || payload.length() > 16_384) {
            throw new IllegalArgumentException("Invalid KIS execution notice encryption parameters.");
        }
        Cipher cipher = Cipher.getInstance("AES/CBC/PKCS5Padding");
        cipher.init(Cipher.DECRYPT_MODE, new SecretKeySpec(keyBytes, "AES"), new IvParameterSpec(ivBytes));
        return new String(cipher.doFinal(Base64.getDecoder().decode(payload)), StandardCharsets.UTF_8);
    }

    public record ExecutionNotice(Market market, String brokerOrderNumber, String symbol, OrderSide side,
                                  int fillQuantity, BigDecimal fillPrice, LocalTime fillTime) { }
}
