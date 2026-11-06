package com.aistocktrader.kis;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** Extracts only the explicitly delimited, sanitized KIS response code from an internal error. */
final class KisResponseCodes {
    private static final Pattern DELIMITED_SAFE_CODE = Pattern.compile("\\(code ([A-Za-z0-9_-]{1,32})\\)");

    private KisResponseCodes() { }

    static String fromErrorMessage(String message, String fallback) {
        if (message == null) return fallback;
        Matcher matcher = DELIMITED_SAFE_CODE.matcher(message);
        return matcher.find() ? matcher.group(1) : fallback;
    }
}
