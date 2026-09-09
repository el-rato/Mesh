# Mesh Engineering Rules

Keep token usage low.

Before editing:
- inspect only relevant files
- avoid broad repo exploration
- do not restate the task

Architecture:
- engines produce evidence, never trades
- trading execution is isolated
- all predictions specify timeframe/horizon
- no future data in features
- chronological validation only
- training and inference use identical preprocessing
- models are versioned
- predictions are logged
- prefer simple solutions
- avoid unrelated refactors

When finished report only:
- files changed
- key change
- tests
- unresolved issues