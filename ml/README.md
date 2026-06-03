# Fight IQ ML

The ML pipeline is intentionally separate from on-demand analysis.

## Training Workflow

```text
completed fights
  -> raw snapshots
  -> normalized fights/stats
  -> leak-proof historical features
  -> model training
  -> model artifact + model_versions row
```

The first target is winner probability. Method prediction comes later.

## Validation

Use time-based validation:

```text
train before 2021 -> test 2021
train before 2022 -> test 2022
train before 2023 -> test 2023
train before 2024 -> test 2024
```

Do not use random split as the main score.

