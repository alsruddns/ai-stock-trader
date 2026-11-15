using System;
using System.Collections.Generic;
using System.Linq;

namespace QuantConnect.Algorithm.CSharp
{
    /// <summary>
    /// Shared production exit rules, kept independent of LEAN runtime types for deterministic tests.
    /// </summary>
    internal static class KisCsvExitResolver
    {
        internal static (decimal Price, string Reason)? ResolveExit(
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
}
