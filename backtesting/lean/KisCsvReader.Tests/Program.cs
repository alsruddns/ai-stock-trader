using QuantConnect.Algorithm.CSharp;

var cases = new (string Name, Func<bool> Run)[]
{
    ("same-bar stop wins over target", SameBarStopWins),
    ("gap-through stop fills at worse open", GapThroughStopUsesOpen),
    ("prior completed bar VWAP loss exits at next open", VwapLossUsesNextOpen),
    ("target gap fills at open", TargetGapUsesOpen),
    ("intrabar target fills at target", IntrabarTargetUsesTarget),
    ("final session bar closes at close", SessionEndUsesClose),
};

var failures = new List<string>();
foreach (var test in cases)
{
    if (!test.Run()) failures.Add(test.Name);
}

if (failures.Count > 0)
{
    Console.Error.WriteLine($"LEAN exit resolver: {cases.Length - failures.Count}/{cases.Length} passed; failed: {string.Join(", ", failures)}");
    return 1;
}

Console.WriteLine($"LEAN exit resolver: {cases.Length}/{cases.Length} passed");
return 0;

static bool SameBarStopWins()
{
    var history = PriorBars(102m, 106m, 99m, 104m);
    var result = KisCsvExitResolver.ResolveExit(history[^1], history, 100m, 104m, 380, 1);
    return result == (100m, "ATR_STOP");
}

static bool GapThroughStopUsesOpen()
{
    var history = PriorBars(99m, 105m, 95m, 101m);
    var result = KisCsvExitResolver.ResolveExit(history[^1], history, 100m, 104m, 380, 1);
    return result == (99m, "ATR_STOP");
}

static bool VwapLossUsesNextOpen()
{
    var history = new List<KisCsvMinuteBar>
    {
        Bar(100m, 101m, 99m, 100m),
        Bar(99m, 100m, 98m, 99m),
        Bar(101m, 102m, 100m, 101m),
    };
    var result = KisCsvExitResolver.ResolveExit(history[^1], history, 90m, 120m, 380, 1);
    return result == (101m, "VWAP_LOSS");
}

static bool TargetGapUsesOpen()
{
    var history = PriorBars(105m, 107m, 104m, 106m);
    var result = KisCsvExitResolver.ResolveExit(history[^1], history, 100m, 104m, 380, 1);
    return result == (105m, "TAKE_PROFIT_GAP");
}

static bool IntrabarTargetUsesTarget()
{
    var history = PriorBars(102m, 105m, 101m, 103m);
    var result = KisCsvExitResolver.ResolveExit(history[^1], history, 100m, 104m, 380, 1);
    return result == (104m, "TAKE_PROFIT");
}

static bool SessionEndUsesClose()
{
    var history = PriorBars(102m, 103m, 101m, 102.5m);
    var result = KisCsvExitResolver.ResolveExit(history[^1], history, 100m, 110m, 3, 1);
    return result == (102.5m, "SESSION_END");
}

static List<KisCsvMinuteBar> PriorBars(decimal open, decimal high, decimal low, decimal close) =>
    new() { Bar(101m, 102m, 100m, 101m), Bar(101m, 102m, 100m, 101m), Bar(open, high, low, close) };

static KisCsvMinuteBar Bar(decimal open, decimal high, decimal low, decimal close) => new()
{
    Open = open,
    High = high,
    Low = low,
    Close = close,
    Volume = 1m,
};
