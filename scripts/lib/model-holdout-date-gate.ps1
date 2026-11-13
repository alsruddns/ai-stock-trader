function Assert-AiTraderHoldoutRangeAfterTraining {
    param(
        [Parameter(Mandatory)] [datetime]$TrainingToDate,
        [Parameter(Mandatory)] [datetime]$FromDate,
        [Parameter(Mandatory)] [datetime]$ToDate
    )

    if ($FromDate.Date -gt $ToDate.Date) {
        throw 'Holdout FromDate must be on or before ToDate.'
    }
    if ($FromDate.Date -le $TrainingToDate.Date) {
        throw 'Independent holdout FromDate must be later than the training cutoff date.'
    }
}
