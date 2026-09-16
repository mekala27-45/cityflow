# Contributing

## Setup

```
uv sync --all-extras
```

## The gates

Every one of these runs in CI and every one of them fails the build.

```
make lint          ruff, mypy strict, sqlfluff, the em dash gate
make test          pytest with an 80 percent coverage floor
make claims        re-derive every published figure from the warehouse
make size          fail if a shipped file exceeds the budget
make palette       re-validate the chart palette in both modes
```

## Conventions

- No em dashes. Commas, colons, parentheses, or "to" for ranges. The gate scans
  every tracked Python, Markdown, SQL, YAML, TOML, TypeScript and CSS file.
- No dual axis charts. There is a test that scans the charting code for two
  scale definitions on one plot.
- Every metric is defined once, in `metrics/metrics.yml`. There is a test that
  fails if the dashboard computes one itself.
- Conventional commits.
