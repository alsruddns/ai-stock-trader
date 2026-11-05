package com.aistocktrader.order;

import jakarta.persistence.LockModeType;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Lock;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import java.util.Optional;

public interface PaperCashRepository extends JpaRepository<PaperCashBalance, Currency> {
    @Lock(LockModeType.PESSIMISTIC_WRITE)
    @Query("select b from PaperCashBalance b where b.currency = :currency")
    Optional<PaperCashBalance> findByCurrencyForUpdate(@Param("currency") Currency currency);
}
