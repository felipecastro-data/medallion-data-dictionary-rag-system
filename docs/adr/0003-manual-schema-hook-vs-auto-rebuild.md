# ADR 0003: Schema check that blocks, instead of automatic rebuilds

- **Status:** Accepted
- **Recorded:** 2026-11-06, describing the final design

## Context

When a lakehouse column is added, dropped or retyped, the dictionary and index need updating. The question is whether that should happen without anyone looking.

An earlier version of the check only reported changes. It now blocks: `medallion-schema-drift` exits 1 until a person accepts the change, `medallion-describe` and `medallion-index` run the same check first, and `make refresh` chains the steps and stops at the first failure.

## Options

1. **Rebuild automatically on any change.** Always current, with no human step.
2. **Report only.** Log the change and carry on, which is easy to miss.
3. **Block until accepted.** The check keeps failing until someone runs `medallion-schema-drift --accept`. After that, one command does the rest.
4. **A Databricks job or event trigger.** Not possible with the project's `sql`-only token, and it isn't needed at this size.

## Decision

Use option 3. It can run on a schedule such as cron, but a schema change always stops for review.

## Trade-offs

**Cost isn't the reason.** A re-run with nothing changed took 4.4 s with no embedding cost. Rebuilding the whole index took 8.0 s and $0.00016, and describing a new column costs about $0.00067. Automatic rebuilds would be affordable.

**Accuracy is the reason.** Syncing is destructive when a column disappears:

- A dropped column's row is deleted from `data_dictionary`, including any description a person reviewed. The text then survives only in the run log and Delta history.
- A renamed column looks like one drop and one add, so an approved description would be replaced by an unreviewed draft without anyone noticing.

**Costs of this choice**

- The dictionary is out of date until someone acts. The block keeps failing, but nothing alerts anyone: it only exits 1 and logs the change, so a scheduled run needs its own alerting.
- The check covers `medallion-describe`, `medallion-index` and `make refresh`. `medallion-describe --dry-run` skips it, because it writes nothing.
- The check compares structure only. It doesn't notice a column whose meaning or values change while its name and type stay the same.
- The baseline is a local, gitignored file. A new checkout accepts whatever schema it finds on its first run.
