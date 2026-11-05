package com.aistocktrader.order;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.util.List;
import java.util.Optional;

public interface PaperPositionRepository extends JpaRepository<PaperPosition, Long> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select p from PaperPosition p where p.market = :market and p.symbol = :symbol and p.currency = :currency")
    Optional<PaperPosition> findInstrumentForUpdate(@Param("market") Market market,
                                                    @Param("symbol") String symbol,
                                                    @Param("currency") Currency currency);
    List<PaperPosition> findAllByQuantityGreaterThanOrderByMarketAscSymbolAsc(int quantity);
}
