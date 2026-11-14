package com.aistocktrader.kis;

import com.fasterxml.jackson.databind.JsonNode;

import java.math.BigDecimal;
import java.util.ArrayList;
import java.util.List;

/** Compares modeled domestic order costs with observed KIS period-profit summary fields. */
final class KisDomesticCostReconciler {
    private static final List<Field> FIELDS = List.of(
            new Field("buy_fee_smtl", "buyCommission"),
            new Field("sll_fee_smtl", "sellCommission"),
            new Field("sll_tltx_smtl", "sellTax"));

    Result reconcile(JsonNode brokerSummary, KisRealizedPnlCalculator.CostEstimate modeled,
                     BigDecimal tolerance) {
        if (brokerSummary == null || modeled == null || !modeled.valid() || tolerance == null
                || tolerance.signum() < 0 || tolerance.compareTo(new BigDecimal("1000")) > 0) {
            return Result.unavailable(List.of("invalid_input"));
        }
        JsonNode summary = singleObject(brokerSummary);
        if (summary == null) return Result.unavailable(List.of("summary_shape"));

        List<String> unavailable = new ArrayList<>();
        List<String> mismatches = new ArrayList<>();
        for (Field field : FIELDS) {
            BigDecimal actual = decimal(summary.get(field.kisField()));
            BigDecimal expected = modeledValue(modeled, field.modeledField());
            if (actual == null || expected == null || actual.signum() < 0 || expected.signum() < 0) {
                unavailable.add(field.kisField());
            } else if (actual.subtract(expected).abs().compareTo(tolerance) > 0) {
                mismatches.add(field.kisField());
            }
        }
        if (!unavailable.isEmpty()) return Result.unavailable(unavailable);
        return mismatches.isEmpty() ? Result.matched() : Result.mismatched(mismatches);
    }

    Result reconcileRealizedPnl(JsonNode brokerSummary, BigDecimal modeledNetPnl, BigDecimal tolerance) {
        if (brokerSummary == null || modeledNetPnl == null || tolerance == null
                || tolerance.signum() < 0 || tolerance.compareTo(new BigDecimal("1000")) > 0) {
            return Result.unavailable(List.of("invalid_input"));
        }
        JsonNode summary = singleObject(brokerSummary);
        if (summary == null) return Result.unavailable(List.of("summary_shape"));
        BigDecimal actual = decimal(summary.get("tot_rlzt_pfls"));
        if (actual == null) return Result.unavailable(List.of("tot_rlzt_pfls"));
        return actual.subtract(modeledNetPnl).abs().compareTo(tolerance) <= 0
                ? Result.matched() : Result.mismatched(List.of("tot_rlzt_pfls"));
    }

    private static JsonNode singleObject(JsonNode summary) {
        if (summary.isObject()) return summary;
        if (summary.isArray() && summary.size() == 1 && summary.get(0).isObject()) return summary.get(0);
        return null;
    }

    private static BigDecimal decimal(JsonNode value) {
        if (value == null || value.isNull() || value.isMissingNode()) return null;
        try {
            if (value.isNumber()) return value.decimalValue();
            if (value.isTextual() && !value.asText().isBlank()) return new BigDecimal(value.asText().trim());
        } catch (NumberFormatException ignored) {
            return null;
        }
        return null;
    }

    private static BigDecimal modeledValue(KisRealizedPnlCalculator.CostEstimate modeled, String field) {
        return switch (field) {
            case "buyCommission" -> modeled.buyCommission();
            case "sellCommission" -> modeled.sellCommission();
            case "sellTax" -> modeled.sellTax();
            default -> null;
        };
    }

    private record Field(String kisField, String modeledField) { }

    record Result(Status status, List<String> unavailableFields, List<String> mismatchedFields) {
        Result {
            unavailableFields = List.copyOf(unavailableFields);
            mismatchedFields = List.copyOf(mismatchedFields);
        }

        static Result matched() { return new Result(Status.MATCHED, List.of(), List.of()); }
        static Result mismatched(List<String> fields) {
            return new Result(Status.MISMATCH, List.of(), fields);
        }
        static Result unavailable(List<String> fields) {
            return new Result(Status.UNAVAILABLE, fields, List.of());
        }
    }

    enum Status { MATCHED, MISMATCH, UNAVAILABLE }
}
