package com.aistocktrader.kis;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.aistocktrader.order.Market;
import jakarta.annotation.PreDestroy;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentMap;
import java.util.concurrent.RejectedExecutionException;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

/** Optional REAL execution-notice listener. It only triggers a read-only REST reconciliation. */
@Component
public class KisRealExecutionNoticeClient {
    private static final Logger log = LoggerFactory.getLogger(KisRealExecutionNoticeClient.class);
    private static final URI REAL_WS_URI = URI.create("ws://ops.koreainvestment.com:21000");
    private static final String DOMESTIC_NOTICE = "H0STCNI0";
    private static final String OVERSEAS_NOTICE = "H0GSCNI0";

    private final boolean enabled;
    private final String htsId;
    private final KisProperties properties;
    private final MarketOperationalScope marketScope;
    private final KisOrderIntentRepository intents;
    private final KisMarketSessionClient sessions;
    private final KisRealOrderOrchestrator orders;
    private final KisRestRateLimiter rateLimiter;
    private final RestClient http;
    private final ObjectMapper mapper;
    private final HttpClient webSocketHttp;
    private final AtomicReference<WebSocket> socket = new AtomicReference<>();
    private final ConcurrentMap<String, CryptoKeys> cryptoKeys = new ConcurrentHashMap<>();
    private final ConcurrentMap<java.util.UUID, Instant> lastQueuedByOrder = new ConcurrentHashMap<>();
    private final ThreadPoolExecutor reconciler = new ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS,
            new ArrayBlockingQueue<>(64), task -> {
                Thread thread = new Thread(task, "kis-real-execution-reconcile");
                thread.setDaemon(true);
                return thread;
            }, new ThreadPoolExecutor.AbortPolicy());
    private volatile long nextConnectionAttemptMillis;
    private volatile int reconnectDelaySeconds = 1;

    public KisRealExecutionNoticeClient(
            @Value("${kis.real-execution-notices-enabled:false}") boolean enabled,
            @Value("${kis.real-hts-id:}") String htsId,
            KisProperties properties, MarketOperationalScope marketScope,
            KisOrderIntentRepository intents,
            KisMarketSessionClient sessions,
            KisRealOrderOrchestrator orders, KisRestRateLimiter rateLimiter,
            RestClient.Builder restClientBuilder, ObjectMapper mapper) {
        this.enabled = enabled;
        this.htsId = htsId == null ? "" : htsId.trim();
        this.properties = properties;
        this.marketScope = marketScope;
        this.intents = intents;
        this.sessions = sessions;
        this.orders = orders;
        this.rateLimiter = rateLimiter;
        this.http = restClientBuilder.build();
        this.mapper = mapper;
        this.webSocketHttp = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(8)).build();
    }

    @Scheduled(fixedDelayString = "${kis.real-execution-notice-connect-poll-ms:5000}")
    public synchronized void connectWhenEnabled() {
        if (!ready() || socket.get() != null || System.currentTimeMillis() < nextConnectionAttemptMillis) return;
        try {
            String approvalKey = requestApprovalKey();
            NoticeListener listener = new NoticeListener();
            WebSocket connected = webSocketHttp.newWebSocketBuilder()
                    .connectTimeout(Duration.ofSeconds(8)).buildAsync(REAL_WS_URI, listener)
                    .get(10, TimeUnit.SECONDS);
            socket.set(connected);
            listener.attach(connected, approvalKey);
            nextConnectionAttemptMillis = System.currentTimeMillis() + TimeUnit.SECONDS.toMillis(5);
            reconnectDelaySeconds = 1;
        } catch (Exception exception) {
            scheduleReconnect();
            log.warn("KIS REAL execution notices unavailable ({})", exception.getClass().getSimpleName());
        }
    }

    private boolean ready() {
        return enabled && properties.accountReadEnabled() && properties.realAccountReadEnabled()
                && properties.credentials(KisEnvironment.REAL).configured()
                && htsId.matches("[A-Za-z0-9_-]{1,32}");
    }

    private String requestApprovalKey() throws InterruptedException {
        KisProperties.Account account = properties.credentials(KisEnvironment.REAL);
        rateLimiter.acquire(KisEnvironment.REAL, account.appKey(), properties.minRequestInterval());
        JsonNode response = http.post()
                .uri(KisEnvironment.REAL.baseUrl() + "/oauth2/Approval")
                .header("content-type", "application/json; charset=utf-8")
                .body(Map.of("grant_type", "client_credentials", "appkey", account.appKey(),
                        "secretkey", account.appSecret()))
                .retrieve().body(JsonNode.class);
        String key = response == null ? "" : response.path("approval_key").asText("");
        if (key.isBlank() || key.length() > 4096) throw new IllegalStateException("KIS WebSocket approval key is unavailable.");
        return key;
    }

    private String subscription(String approvalKey, String transactionId) {
        try {
            return mapper.writeValueAsString(Map.of(
                    "header", Map.of("approval_key", approvalKey, "custtype", "P", "tr_type", "1",
                            "content-type", "utf-8"),
                    "body", Map.of("input", Map.of("tr_id", transactionId, "tr_key", htsId))));
        } catch (Exception exception) {
            throw new IllegalStateException("KIS execution notice subscription could not be encoded.");
        }
    }

    List<String> subscriptionTransactionIds() {
        List<String> transactionIds = new ArrayList<>(2);
        if (marketScope.allowedMarkets().contains(Market.KR)) transactionIds.add(DOMESTIC_NOTICE);
        if (marketScope.allowedMarkets().contains(Market.US)) transactionIds.add(OVERSEAS_NOTICE);
        return List.copyOf(transactionIds);
    }

    private Market marketForNotice(String transactionId) {
        if (DOMESTIC_NOTICE.equals(transactionId)) return Market.KR;
        if (OVERSEAS_NOTICE.equals(transactionId)) return Market.US;
        return null;
    }

    private void acceptControl(JsonNode message) {
        JsonNode header = message.path("header");
        JsonNode body = message.path("body");
        String transactionId = header.path("tr_id").asText("");
        Market market = marketForNotice(transactionId);
        if (market == null || !marketScope.allowedMarkets().contains(market)) return;
        JsonNode output = body.path("output");
        String key = output.path("key").asText("");
        String iv = output.path("iv").asText("");
        if (key.length() == 32 && iv.length() == 16) cryptoKeys.put(transactionId, new CryptoKeys(key, iv));
    }

    private void acceptFrame(String frame) {
        String[] parts = frame.split("\\|", 4);
        if (parts.length != 4 || !"1".equals(parts[0])) return;
        Market market = marketForNotice(parts[1]);
        if (market == null || !marketScope.allowedMarkets().contains(market)) return;
        CryptoKeys keys = cryptoKeys.get(parts[1]);
        if (keys == null) return;
        KisExecutionNoticeParser.parseReal(parts[1], parts[3], keys.key(), keys.iv(), htsId,
                properties.credentials(KisEnvironment.REAL).accountNumber()).ifPresent(this::queueReconciliation);
    }

    private void queueReconciliation(KisExecutionNoticeParser.ExecutionNotice notice) {
        if (!marketScope.allowedMarkets().contains(notice.market())) return;
        intents.findByEnvironmentAndMarketAndBrokerOrderNumber(KisEnvironment.REAL, notice.market(),
                        notice.brokerOrderNumber())
                .filter(intent -> intent.getSymbol().equalsIgnoreCase(notice.symbol())
                        && intent.getSide() == notice.side())
                .ifPresent(intent -> {
                    try {
                        var session = sessions.session(notice.market());
                        Instant fillAt = KisExecutionNoticeTime.resolve(notice, session, Instant.now()).orElse(null);
                        if (fillAt == null) return;
                        if (intents.recordRealFillTime(intent.getIdempotencyKey(), fillAt) != 1) return;
                    } catch (RuntimeException exception) {
                        log.warn("KIS REAL fill timestamp unavailable ({})", exception.getClass().getSimpleName());
                        return;
                    }
                    Instant now = Instant.now();
                    Instant previous = lastQueuedByOrder.compute(intent.getIdempotencyKey(), (key, prior) ->
                            prior == null || Duration.between(prior, now).toMillis() >= 1_000 ? now : prior);
                    if (previous != now) return;
                    try {
                        reconciler.execute(() -> {
                            try {
                                orders.reconcile(intent.getIdempotencyKey());
                            } catch (RuntimeException exception) {
                                log.warn("KIS REAL fill reconciliation unavailable for {} {} ({})",
                                        intent.getMarket(), intent.getSymbol(), exception.getClass().getSimpleName());
                            }
                        });
                    } catch (RejectedExecutionException exception) {
                        lastQueuedByOrder.remove(intent.getIdempotencyKey(), now);
                        log.warn("KIS REAL fill reconciliation queue is full; periodic polling remains active.");
                    }
                });
    }

    private void scheduleReconnect() {
        socket.set(null);
        cryptoKeys.clear();
        long wait = Math.min(reconnectDelaySeconds, 60);
        nextConnectionAttemptMillis = System.currentTimeMillis() + TimeUnit.SECONDS.toMillis(wait);
        reconnectDelaySeconds = (int) Math.min((long) reconnectDelaySeconds * 2, 60);
    }

    @PreDestroy
    public void close() {
        WebSocket active = socket.getAndSet(null);
        if (active != null) active.sendClose(WebSocket.NORMAL_CLOSURE, "shutdown");
        reconciler.shutdownNow();
    }

    private final class NoticeListener implements WebSocket.Listener {
        private final StringBuilder text = new StringBuilder();
        private volatile WebSocket activeSocket;
        private volatile String approvalKey;

        void attach(WebSocket webSocket, String key) {
            activeSocket = webSocket;
            approvalKey = key;
            for (String transactionId : subscriptionTransactionIds()) {
                webSocket.sendText(subscription(key, transactionId), true);
            }
            webSocket.request(1);
        }

        @Override
        public void onOpen(WebSocket webSocket) {
            webSocket.request(1);
        }

        @Override
        public synchronized CompletableFuture<?> onText(WebSocket webSocket, CharSequence data, boolean last) {
            text.append(data);
            if (text.length() > 100_000) {
                text.setLength(0);
                scheduleReconnect();
            } else if (last) {
                String frame = text.toString();
                text.setLength(0);
                if (frame.startsWith("1|")) acceptFrame(frame);
                else {
                    try { acceptControl(mapper.readTree(frame)); }
                    catch (Exception ignored) { /* Ignore non-control text without logging its content. */ }
                }
            }
            webSocket.request(1);
            return CompletableFuture.completedFuture(null);
        }

        @Override
        public CompletableFuture<?> onPing(WebSocket webSocket, ByteBuffer message) {
            webSocket.sendPong(message);
            webSocket.request(1);
            return CompletableFuture.completedFuture(null);
        }

        @Override
        public CompletableFuture<?> onClose(WebSocket webSocket, int statusCode, String reason) {
            if (socket.compareAndSet(webSocket, null)) scheduleReconnect();
            return CompletableFuture.completedFuture(null);
        }

        @Override
        public void onError(WebSocket webSocket, Throwable error) {
            if (socket.compareAndSet(webSocket, null)) scheduleReconnect();
        }
    }

    private record CryptoKeys(String key, String iv) { }
}
