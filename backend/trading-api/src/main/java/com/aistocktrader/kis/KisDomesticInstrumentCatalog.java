package com.aistocktrader.kis;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/** Reads the local, credential-free symbol catalog generated from KIS official master files. */
final class KisDomesticInstrumentCatalog {
    private static final Duration MAX_AGE = Duration.ofDays(7);
    private static final Map<Path, CachedCatalog> CACHE = new ConcurrentHashMap<>();

    private KisDomesticInstrumentCatalog() { }

    static Instrument require(String symbol) {
        Path path = configuredPath();
        try {
            CachedCatalog cached = CACHE.get(path);
            long modified = Files.getLastModifiedTime(path).toMillis();
            if (cached == null || cached.modifiedAtMillis() != modified) {
                cached = load(path, modified);
                CACHE.put(path, cached);
            }
            Instant now = Instant.now();
            Instant fileUpdatedAt = Instant.ofEpochMilli(cached.modifiedAtMillis());
            Duration age = Duration.between(fileUpdatedAt, now);
            if (age.isNegative() || age.compareTo(MAX_AGE) > 0
                    || cached.generatedAt().isAfter(now.plus(Duration.ofMinutes(5)))) {
                throw new IllegalArgumentException("Domestic KIS symbol master is stale; refresh it before placing orders.");
            }
            Instrument instrument = cached.instruments().get(symbol);
            if (instrument == null) {
                throw new IllegalArgumentException("Domestic symbol is missing from the current KIS master.");
            }
            return instrument;
        } catch (IOException | RuntimeException ex) {
            if (ex instanceof IllegalArgumentException illegalArgumentException) throw illegalArgumentException;
            throw new IllegalArgumentException("A current KIS domestic symbol master is required before placing orders.", ex);
        }
    }

    private static Path configuredPath() {
        String configured = System.getProperty("kis.domestic.symbol-master-path");
        if (configured == null || configured.isBlank()) configured = System.getenv("KIS_DOMESTIC_SYMBOL_MASTER_PATH");
        if (configured == null || configured.isBlank()) configured = ".local/reference-data/kis-domestic-symbols.csv";
        return Path.of(configured).toAbsolutePath().normalize();
    }

    private static CachedCatalog load(Path path, long modifiedAtMillis) throws IOException {
        var lines = Files.readAllLines(path, StandardCharsets.UTF_8);
        if (lines.size() < 3 || !lines.get(0).startsWith("#generated_at=")
                || !lines.get(1).equals("symbol,market,group_code")) {
            throw new IllegalArgumentException("KIS domestic symbol master has an invalid header.");
        }
        Instant generatedAt;
        try {
            generatedAt = Instant.parse(lines.get(0).substring("#generated_at=".length()));
        } catch (RuntimeException ex) {
            throw new IllegalArgumentException("KIS domestic symbol master timestamp is invalid.", ex);
        }
        Map<String, Instrument> instruments = new HashMap<>();
        for (int index = 2; index < lines.size(); index++) {
            String[] fields = lines.get(index).split(",", -1);
            if (fields.length != 3 || !fields[0].matches("\\d{6}")
                    || !(fields[1].equals("KOSPI") || fields[1].equals("KOSDAQ"))
                    || !fields[2].matches("[A-Z0-9)]{1,2}")) {
                throw new IllegalArgumentException("KIS domestic symbol master contains an invalid row.");
            }
            if (instruments.putIfAbsent(fields[0], new Instrument(fields[1], fields[2])) != null) {
                throw new IllegalArgumentException("KIS domestic symbol master contains duplicate symbols.");
            }
        }
        if (instruments.isEmpty()) throw new IllegalArgumentException("KIS domestic symbol master is empty.");
        return new CachedCatalog(generatedAt, modifiedAtMillis, Map.copyOf(instruments));
    }

    record Instrument(String market, String groupCode) { }
    private record CachedCatalog(Instant generatedAt, long modifiedAtMillis, Map<String, Instrument> instruments) { }
}
