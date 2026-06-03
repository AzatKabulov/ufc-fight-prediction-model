# Fight IQ Scrapers

Scrapers should be polite, source-specific adapters. They should return structured source snapshots and avoid hiding parsing decisions inside model code.

## Initial Sources

- UFC.com for official event/fighter presentation data when available.
- UFCStats for detailed fight and fighter statistics.
- Manual admin inputs for fragile or missing upcoming-card data.

## Rule

Light event monitoring is allowed. Deep fighter refresh happens only when a fighter is booked, manually requested, or needed for model training.

