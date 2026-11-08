using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text.RegularExpressions;
using QuantConnect.Data;
using QuantConnect.Data.Market;
using QuantConnect.Interfaces;
using QuantConnect.Orders;
using QuantConnect.Orders.Fees;
using QuantConnect.Orders.Fills;
using QuantConnect.Securities;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Reads a reviewed, local KIS minute-bar CSV through LEAN's real file data feed.
    /// This ingestion smoke algorithm intentionally sends no orders.
    /// </summary>
    public class KisCsvLeanSmokeAlgorithm : QCAlgorithm
    {
        private Symbol _symbol;
        private int _bars;
        private DateTime? _firstUtc;
        private DateTime? _lastUtc;
        private int _expectedBars;

        public override void Initialize()
        {
            var sessionText = GetParameter("session");
            if (string.IsNullOrWhiteSpace(sessionText)) sessionText = "2026-11-06";
            var session = DateTime.ParseExact(sessionText, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            _expectedBars = int.TryParse(GetParameter("expected-bars"), out var expected) ? expected : 390;

            SetTimeZone(TimeZones.Utc);
            SetStartDate(session.Year, session.Month, session.Day);
            SetEndDate(session.Year, session.Month, session.Day);
            SetCash(100_000);
            _symbol = AddData<KisCsvMinuteBar>("KISCSV", Resolution.Minute, TimeZones.Utc).Symbol;
        }

        public override void OnData(Slice slice)
        {
            if (!slice.TryGetValue(_symbol, out var value) || value is not KisCsvMinuteBar bar) return;
            _bars++;
            _firstUtc ??= bar.Time;
            _lastUtc = bar.Time;
        }

        public override void OnEndOfAlgorithm()
        {
            Debug($"KIS_LOCAL_CSV bars={_bars} first_utc={_firstUtc:O} last_utc={_lastUtc:O} orders={Transactions.GetOrders().Count()}");
            if (_bars != _expectedBars)
            {
                throw new InvalidOperationException($"Expected {_expectedBars} local KIS bars, received {_bars}");
            }
        }
    }

    /// <summary>
    /// TradeBar custom-data adapter for `symbol,timestamp,open,high,low,close,volume,spread_bps` CSV rows.
    /// </summary>
    public class KisCsvMinuteBar : TradeBar
    {
        public decimal SpreadBps { get; private set; }
        public bool HasSpread { get; private set; }

        public override SubscriptionDataSource GetSource(SubscriptionDataConfig config, DateTime date, bool isLiveMode)
        {
            var path = Path.Combine(Globals.DataFolder, "kis-minute-bars.csv");
            return new SubscriptionDataSource(path, SubscriptionTransportMedium.LocalFile, FileFormat.Csv);
        }

        public override BaseData Reader(SubscriptionDataConfig config, string line, DateTime date, bool isLiveMode)
        {
            if (string.IsNullOrWhiteSpace(line) || line.StartsWith("symbol,", StringComparison.OrdinalIgnoreCase)) return null;
            var fields = line.Split(',');
            if (fields.Length < 7) return null;

            try
            {
                var instant = DateTimeOffset.Parse(fields[1], CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind);
                var time = instant.UtcDateTime;
                if (time.Date != date.Date) return null;
                var open = decimal.Parse(fields[2], CultureInfo.InvariantCulture);
                var high = decimal.Parse(fields[3], CultureInfo.InvariantCulture);
                var low = decimal.Parse(fields[4], CultureInfo.InvariantCulture);
                var close = decimal.Parse(fields[5], CultureInfo.InvariantCulture);
                var volume = decimal.Parse(fields[6], CultureInfo.InvariantCulture);
                var hasSpread = fields.Length > 7 && !string.IsNullOrWhiteSpace(fields[7]);
                var spread = hasSpread
                    ? decimal.Parse(fields[7], CultureInfo.InvariantCulture)
                    : 0m;

                return new KisCsvMinuteBar
                {
                    Symbol = config.Symbol,
                    Time = time,
                    EndTime = time.AddMinutes(1),
                    Period = TimeSpan.FromMinutes(1),
                    Open = open,
                    High = high,
                    Low = low,
                    Close = close,
                    Volume = volume,
                    Value = close,
                    SpreadBps = spread,
                    HasSpread = hasSpread
                };
            }
            catch (Exception exception) when (exception is FormatException || exception is OverflowException || exception is ArgumentException)
            {
                return null;
            }
        }

        public override NodaTime.DateTimeZone DataTimeZone() => TimeZones.Utc;
        public override Resolution DefaultResolution() => Resolution.Minute;
        public override List<Resolution> SupportedResolutions() => new() { Resolution.Minute };
        public override bool RequiresMapping() => false;
        public override bool IsSparseData() => false;
    }

    /// <summary>
    /// Event-driven replay of the repository ORB/VWAP/EMA/ATR strategy using LEAN's local data feed.
    /// The simulated orders are local backtest orders and have no KIS brokerage connection.
    /// </summary>
    public class KisCsvLeanStrategyAlgorithm : QCAlgorithm
    {
        private const decimal CommissionBps = 5m;
        private const decimal SlippageBps = 5m;
        private const decimal MaximumSpreadBps = 15m;
        private const decimal MinimumTurnover = 1_000_000m;
        private const int OpeningRangeBars = 5;
        private const int EmaPeriod = 20;
        private const int AtrPeriod = 14;
        private const int RelativeVolumePeriod = 20;
        private const decimal AtrStopMultiple = 1.5m;
        private const decimal RewardRiskMultiple = 2m;

        private readonly List<KisCsvMinuteBar> _bars = new();
        private Dictionary<DateTime, decimal> _aiScores;
        private Symbol _symbol;
        private decimal _openingHigh;
        private decimal _cumulativeTypicalVolume;
        private decimal _cumulativeVolume;
        private decimal? _entryPrice;
        private decimal? _stopPrice;
        private decimal? _targetPrice;
        private decimal? _pendingEntrySpreadBps;
        private int _entryBarIndex = -1;
        private int _signals;
        private int _quantSignals;
        private int _trades;
        private int _expectedBars;
        private int _expectedAiScoreCount;
        private string _market;
        private string _sourceSymbol;
        private string _currency;
        private string _aiModelId;
        private decimal? _aiThreshold;
        private decimal _buyCommissionBps = 5m;
        private decimal _sellCommissionBps = 5m;
        private decimal _sellTaxBps;
        private decimal _slippageBps = 5m;
        private bool _entryPending;

        public override void Initialize()
        {
            _market = (GetParameter("market") ?? "US").ToUpperInvariant();
            if (_market != "US" && _market != "KR") throw new ArgumentException("market must be US or KR");
            _sourceSymbol = GetParameter("symbol") ?? (_market == "US" ? "AAPL" : "005930");
            _currency = GetParameter("currency") ?? (_market == "US" ? "USD" : "KRW");
            _buyCommissionBps = ReadNonNegativeCostParameter("buy-commission-bps", 5m);
            _sellCommissionBps = ReadNonNegativeCostParameter("sell-commission-bps", 5m);
            _sellTaxBps = ReadNonNegativeCostParameter("sell-tax-bps", 0m);
            _slippageBps = ReadNonNegativeCostParameter("slippage-bps", 5m);
            var sessionText = GetParameter("session");
            if (string.IsNullOrWhiteSpace(sessionText)) throw new ArgumentException("session parameter is required");
            var session = DateTime.ParseExact(sessionText, "yyyy-MM-dd", CultureInfo.InvariantCulture);
            _expectedBars = int.TryParse(GetParameter("expected-bars"), out var expected)
                ? expected
                : (_market == "US" ? 390 : 380);
            _aiModelId = (GetParameter("ai-model-id") ?? "").Trim();
            if (!string.IsNullOrEmpty(_aiModelId))
            {
                if (!Regex.IsMatch(_aiModelId, @"^(lightgbm|xgboost)-[a-f0-9]{32}$"))
                    throw new ArgumentException("AI model identifier is invalid");
                if (!decimal.TryParse(GetParameter("ai-threshold"), NumberStyles.Float,
                        CultureInfo.InvariantCulture, out var threshold) || threshold < 0m || threshold > 1m)
                    throw new ArgumentException("AI model threshold must be between zero and one");
                _aiThreshold = threshold;
                _expectedAiScoreCount = int.TryParse(GetParameter("ai-score-count"), out var scoreCount)
                    ? scoreCount
                    : -1;
                if (_expectedAiScoreCount != _expectedBars - 20)
                    throw new ArgumentException("AI score count does not cover the complete session warmup range");
                var scorePath = Path.Combine(Globals.DataFolder, "kis-ai-scores.csv");
                var scoreBytes = File.ReadAllBytes(scorePath);
                var actualHash = Convert.ToHexString(SHA256.HashData(scoreBytes)).ToLowerInvariant();
                var expectedHash = (GetParameter("ai-score-sha256") ?? "").Trim().ToLowerInvariant();
                if (!Regex.IsMatch(expectedHash, @"^[a-f0-9]{64}$") || actualHash != expectedHash)
                    throw new ArgumentException("AI score file hash does not match the prepared manifest");
                _aiScores = LoadAiScores(scorePath);
                if (_aiScores.Count != _expectedAiScoreCount)
                    throw new ArgumentException("AI score file has an invalid or duplicate timestamp count");
            }

            SetTimeZone(TimeZones.Utc);
            SetStartDate(session.Year, session.Month, session.Day);
            SetEndDate(session.Year, session.Month, session.Day);
            SetAccountCurrency(_currency);
            SetCash(100_000m);
            _symbol = AddData<KisCsvMinuteBar>("KISCSV", Resolution.Minute, TimeZones.Utc).Symbol;
            Securities[_symbol].SetFillModel(new KisCsvCostFillModel(_slippageBps));
            Securities[_symbol].SetFeeModel(new KisCsvCommissionFeeModel(
                _currency, _buyCommissionBps, _sellCommissionBps, _sellTaxBps, _slippageBps));
        }

        public override void OnData(Slice slice)
        {
            if (!slice.TryGetValue(_symbol, out var value) || value is not KisCsvMinuteBar bar) return;
            _bars.Add(bar);
            if (_aiScores != null && _bars.Count >= 21 && !_aiScores.ContainsKey(bar.Time))
                throw new InvalidOperationException("AI score is missing for a complete-session timestamp");
            if (_bars.Count <= OpeningRangeBars) _openingHigh = Math.Max(_openingHigh, bar.High);
            _cumulativeTypicalVolume += ((bar.High + bar.Low + bar.Close) / 3m) * bar.Volume;
            _cumulativeVolume += bar.Volume;

            if (_entryPending)
            {
                _entryPending = false;
                var signalSpreadBps = _pendingEntrySpreadBps
                    ?? throw new InvalidOperationException("Entry signal did not retain its observed spread");
                _pendingEntrySpreadBps = null;
                if (signalSpreadBps < 0m || signalSpreadBps > MaximumSpreadBps)
                    throw new InvalidOperationException("Entry signal spread is outside the configured limit");
                var signalHistory = _bars.Take(_bars.Count - 1).ToList();
                var risk = Atr(signalHistory, AtrPeriod) * AtrStopMultiple;
                var entryCostsBps = _slippageBps + signalSpreadBps / 2m;
                var expectedFill = bar.Open * (1m + entryCostsBps / 10_000m);
                _entryPrice = expectedFill;
                _stopPrice = expectedFill - risk;
                _targetPrice = expectedFill + risk * RewardRiskMultiple;
                _entryBarIndex = _bars.Count - 1;
                MarketOrder(_symbol, 1m, false,
                    $"ENTRY:{signalSpreadBps.ToString(CultureInfo.InvariantCulture)}");
            }

            if (_entryPrice.HasValue)
            {
                var exit = ResolveExit(bar, _bars, _stopPrice.Value, _targetPrice.Value,
                    _expectedBars, _entryBarIndex);
                if (exit.HasValue)
                {
                    var exitSpreadBps = exit.Value.Reason == "SESSION_END"
                        ? (bar.HasSpread ? bar.SpreadBps : MaximumSpreadBps)
                        : (_bars.Count > 1 && _bars[_bars.Count - 2].HasSpread
                            ? _bars[_bars.Count - 2].SpreadBps : MaximumSpreadBps);
                    MarketOrder(_symbol, -1m, false,
                        $"EXIT:{exit.Value.Price.ToString(CultureInfo.InvariantCulture)}:{exit.Value.Reason}:{exitSpreadBps.ToString(CultureInfo.InvariantCulture)}");
                    _entryPrice = null;
                    _stopPrice = null;
                    _targetPrice = null;
                    _entryBarIndex = -1;
                    _trades++;
                    return;
                }
            }

            if (!_entryPrice.HasValue && !_entryPending
                && IsEntryCandidate(_bars, _openingHigh, _cumulativeTypicalVolume, _cumulativeVolume))
            {
                _quantSignals++;
                var passesAi = true;
                if (_aiScores != null)
                {
                    if (!_aiScores.TryGetValue(bar.Time, out var probability))
                        throw new InvalidOperationException("AI score is missing for a quant candidate bar");
                    passesAi = probability >= _aiThreshold.Value;
                }
                if (passesAi)
                {
                    _signals++;
                    if (_bars.Count < _expectedBars)
                    {
                        _pendingEntrySpreadBps = bar.SpreadBps;
                        _entryPending = true;
                    }
                }
            }
        }

        public override void OnEndOfAlgorithm()
        {
            var orderCount = Transactions.GetOrders().Count();
            var netPnl = Portfolio.TotalPortfolioValue - 100_000m;
            Debug($"KIS_LEAN_ORB_VWAP market={_market} symbol={_sourceSymbol} bars={_bars.Count} signals={_signals} quant_signals={_quantSignals} trades={_trades} orders={orderCount} ai_model_id={(_aiModelId ?? "")} ai_threshold={(_aiThreshold?.ToString(CultureInfo.InvariantCulture) ?? "")} net_liquidation={Portfolio.TotalPortfolioValue.ToString(CultureInfo.InvariantCulture)} net_pnl={netPnl.ToString(CultureInfo.InvariantCulture)}");
            if (_bars.Count != _expectedBars) throw new InvalidOperationException($"Expected {_expectedBars} bars for a complete {_market} session, received {_bars.Count}");
            if (_aiScores != null && _aiScores.Count != _expectedAiScoreCount)
                throw new InvalidOperationException($"Expected {_expectedAiScoreCount} timestamp-aligned AI scores, received {_aiScores.Count}");
            if (Portfolio.Invested || _entryPending) throw new InvalidOperationException("Backtest ended with an open or pending position");
        }

        private static Dictionary<DateTime, decimal> LoadAiScores(string path)
        {
            var scores = new Dictionary<DateTime, decimal>();
            foreach (var line in File.ReadLines(path).Skip(1))
            {
                var fields = line.Split(',');
                if (fields.Length != 2
                    || !DateTimeOffset.TryParse(fields[0], CultureInfo.InvariantCulture, DateTimeStyles.RoundtripKind, out var timestamp)
                    || !decimal.TryParse(fields[1], NumberStyles.Float, CultureInfo.InvariantCulture, out var probability)
                    || probability < 0m || probability > 1m
                    || !scores.TryAdd(timestamp.UtcDateTime, probability))
                    throw new ArgumentException("AI score file contains malformed, invalid, or duplicate rows");
            }
            return scores;
        }

        private decimal ReadNonNegativeCostParameter(string name, decimal fallback)
        {
            var raw = GetParameter(name);
            if (string.IsNullOrWhiteSpace(raw)) return fallback;
            if (!decimal.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out var value)
                || value < 0m || value > 10_000m)
                throw new ArgumentException($"{name} must be a decimal basis-point value from zero through 10000");
            return value;
        }

        private static bool IsEntryCandidate(IReadOnlyList<KisCsvMinuteBar> bars, decimal openingHigh, decimal cumulativeTypicalVolume, decimal cumulativeVolume)
        {
            if (bars.Count < Math.Max(Math.Max(OpeningRangeBars, EmaPeriod), Math.Max(AtrPeriod, RelativeVolumePeriod + 1))) return false;
            var current = bars[bars.Count - 1];
            var priorVolumes = bars.Skip(Math.Max(0, bars.Count - RelativeVolumePeriod - 1))
                .Take(Math.Min(RelativeVolumePeriod, bars.Count - 1)).Select(item => item.Volume).ToList();
            var averageVolume = priorVolumes.Count == 0 ? 0m : priorVolumes.Average();
            var relativeVolume = averageVolume == 0m ? 0m : current.Volume / averageVolume;
            var vwap = cumulativeVolume == 0m ? current.Close : cumulativeTypicalVolume / cumulativeVolume;
            var ema = Ema(bars.Select(item => item.Close).ToList());
            return current.Close > openingHigh && current.Close > vwap && current.Close > ema
                && relativeVolume >= 1.5m && current.HasSpread && current.SpreadBps <= MaximumSpreadBps
                && current.Close * current.Volume >= MinimumTurnover;
        }

        private static decimal Ema(IReadOnlyList<decimal> values)
        {
            if (values.Count < EmaPeriod)
                throw new InvalidOperationException("EMA history is shorter than its configured period");
            var alpha = 2m / (EmaPeriod + 1m);
            var result = values.Take(EmaPeriod).Average();
            for (var i = EmaPeriod; i < values.Count; i++)
                result = values[i] * alpha + result * (1m - alpha);
            return result;
        }

        private static decimal Atr(IReadOnlyList<KisCsvMinuteBar> bars, int period)
        {
            var ranges = new List<decimal>();
            for (var i = 0; i < bars.Count; i++)
            {
                var previousClose = i == 0 ? bars[i].Close : bars[i - 1].Close;
                ranges.Add(Math.Max(bars[i].High - bars[i].Low,
                    Math.Max(Math.Abs(bars[i].High - previousClose), Math.Abs(bars[i].Low - previousClose))));
            }
            return ranges.Skip(Math.Max(0, ranges.Count - period)).Average();
        }

        private static (decimal Price, string Reason)? ResolveExit(
            KisCsvMinuteBar bar, IReadOnlyList<KisCsvMinuteBar> history, decimal stop,
            decimal target, int expectedBars, int entryBarIndex)
        {
            var currentBarIndex = history.Count - 1;
            if (currentBarIndex > entryBarIndex)
            {
                var previous = history[currentBarIndex - 1];
                var completedHistory = history.Take(currentBarIndex).ToList();
                var completedVolume = completedHistory.Sum(item => item.Volume);
                var completedTypicalVolume = completedHistory.Sum(item =>
                    ((item.High + item.Low + item.Close) / 3m) * item.Volume);
                var completedVwap = completedVolume == 0m
                    ? previous.Close : completedTypicalVolume / completedVolume;
                if (previous.Close < completedVwap) return (bar.Open, "VWAP_LOSS");
            }
            if (bar.Open <= stop || bar.Low <= stop) return (Math.Min(bar.Open, stop), "ATR_STOP");
            if (bar.Open >= target) return (bar.Open, "TAKE_PROFIT_GAP");
            if (bar.High >= target) return (target, "TAKE_PROFIT");
            if (history.Count == expectedBars) return (bar.Close, "SESSION_END");
            return null;
        }
    }

    internal static class KisCsvExecutionCostInputs
    {
        private const decimal MaximumSpreadBps = 15m;

        public static decimal RawPrice(string tag, KisCsvMinuteBar bar)
        {
            var fields = tag.Split(':');
            if (fields[0] == "EXIT")
            {
                if (fields.Length != 4
                    || !decimal.TryParse(fields[1], NumberStyles.Float, CultureInfo.InvariantCulture, out var exitPrice))
                    throw new ArgumentException("Exit order tag has invalid price or execution spread");
                return exitPrice;
            }
            if (fields[0] == "ENTRY") return bar.Open;
            return bar.Open;
        }

        public static decimal SpreadBps(string tag, KisCsvMinuteBar bar)
        {
            var fields = tag.Split(':');
            string? explicitSpread = fields[0] switch
            {
                "ENTRY" when fields.Length == 2 => fields[1],
                "EXIT" when fields.Length == 4 => fields[3],
                "ENTRY" or "EXIT" => throw new ArgumentException("Order tag has invalid execution spread fields"),
                _ => null,
            };
            if (explicitSpread is not null)
            {
                if (!decimal.TryParse(explicitSpread, NumberStyles.Float, CultureInfo.InvariantCulture, out var spread)
                    || spread < 0m)
                    throw new ArgumentException("Order tag has an invalid execution spread");
                return spread;
            }
            return bar.HasSpread ? bar.SpreadBps : MaximumSpreadBps;
        }
    }

    public class KisCsvCostFillModel : FillModel
    {
        private readonly decimal _slippageBps;

        public KisCsvCostFillModel(decimal slippageBps)
        {
            _slippageBps = slippageBps;
        }

        public override OrderEvent MarketFill(Security asset, MarketOrder order)
        {
            var fill = base.MarketFill(asset, order);
            if (fill.Status != OrderStatus.Filled || asset.GetLastData() is not KisCsvMinuteBar bar) return fill;
            var rawPrice = KisCsvExecutionCostInputs.RawPrice(order.Tag, bar);
            var effectiveSpreadBps = KisCsvExecutionCostInputs.SpreadBps(order.Tag, bar);
            var costsBps = _slippageBps + effectiveSpreadBps / 2m;
            fill.FillPrice = order.Direction == OrderDirection.Buy
                ? rawPrice * (1m + costsBps / 10_000m)
                : rawPrice * (1m - costsBps / 10_000m);
            return fill;
        }
    }

    public class KisCsvCommissionFeeModel : FeeModel
    {
        private readonly string _currency;
        private readonly decimal _buyCommissionBps;
        private readonly decimal _sellCommissionBps;
        private readonly decimal _sellTaxBps;
        private readonly decimal _slippageBps;

        public KisCsvCommissionFeeModel(string currency, decimal buyCommissionBps,
            decimal sellCommissionBps, decimal sellTaxBps, decimal slippageBps)
        {
            _currency = currency;
            _buyCommissionBps = buyCommissionBps;
            _sellCommissionBps = sellCommissionBps;
            _sellTaxBps = sellTaxBps;
            _slippageBps = slippageBps;
        }

        public override OrderFee GetOrderFee(OrderFeeParameters parameters)
        {
            if (parameters.Security.GetLastData() is not KisCsvMinuteBar bar) return OrderFee.Zero;
            var rawPrice = KisCsvExecutionCostInputs.RawPrice(parameters.Order.Tag, bar);
            var effectiveSpreadBps = KisCsvExecutionCostInputs.SpreadBps(parameters.Order.Tag, bar);
            var costsBps = _slippageBps + effectiveSpreadBps / 2m;
            var fillPrice = parameters.Order.Direction == OrderDirection.Buy
                ? rawPrice * (1m + costsBps / 10_000m)
                : rawPrice * (1m - costsBps / 10_000m);
            var commissionAndTaxBps = parameters.Order.Direction == OrderDirection.Buy
                ? _buyCommissionBps : _sellCommissionBps + _sellTaxBps;
            return new OrderFee(new CashAmount(
                Math.Abs(parameters.Order.Quantity) * fillPrice * commissionAndTaxBps / 10_000m, _currency));
        }
    }
}
