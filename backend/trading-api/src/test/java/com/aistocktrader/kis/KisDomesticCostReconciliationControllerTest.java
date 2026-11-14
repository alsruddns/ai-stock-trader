package com.aistocktrader.kis;

import com.aistocktrader.order.Market;
import org.junit.jupiter.api.Test;

import java.time.LocalDate;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verifyNoInteractions;

class KisDomesticCostReconciliationControllerTest {
    @Test
    void blocksDomesticReconciliationWhenMarketIsOutsideOperationalScope() {
        KisDomesticCostReconciliationService service = mock(KisDomesticCostReconciliationService.class);
        KisDomesticCostReconciliationController controller = new KisDomesticCostReconciliationController(
                service, new MarketOperationalScope("US"));

        KisApiException exception = assertThrows(KisApiException.class,
                () -> controller.reconcile(LocalDate.of(2026, 11, 12)));

        assertEquals(org.springframework.http.HttpStatus.LOCKED, exception.status());
        verifyNoInteractions(service);
    }
}
