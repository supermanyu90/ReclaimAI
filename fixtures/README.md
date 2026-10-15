# fixtures

`demo.json` is the single source of synthetic demo data. Everything in it is fictional.

| Make target | Result |
|---|---|
| `make seed` | "before" state: Patel tenant has 6 assets (demo start) |
| `make seed-after` | adds the FD confirmed via chat (proposal + confirmation + outbox event): 7 assets |
| `make reset` | wipes only the synthetic demo tenants, then reseeds "before" |
| `make demo-reset` | `reset` plus: deletes those tenants' local files, reloads the synthetic transaction feed and predictions (the demo start) |

IDs are uuid5 values derived from fixture keys, so they are identical on every machine
(`reclaim_api.seed.fixture_id("ramesh")`). Re-seeding a state is a no-op. Going from
"after" back to "before" requires `make reset`.

Local sign-in for API calls: `Authorization: Bearer dev:<fixture_id(user_key)>`.

## What the fixture deliberately contains

| Case | Where |
|---|---|
| Old statement (2023) | Savings account, Sagar Bharat Bank |
| Unknown nomination | Term life policy (schedule's nominee section illegible) |
| Nomination recorded vs not recorded vs not applicable | Savings / mutual fund / property |
| Repeated statement (same hash) | Mutual fund statement re-upload, `duplicate_of` set |
| Contradictory amount | Mutual fund: owner said ₹9,00,000, statement says ₹8,75,400 |
| Correction creates a new version | Pension: v1 user-reported, v2 corrected from statement |
| Separate amount kinds | Life cover, SIP installment, property estimate and home-loan liability |
| Separate currencies | SGD savings at Merlion Harbour Bank (Singapore) |
| Family relation ≠ nominee ≠ grant | Priya: daughter + trusted contact; nominee only on savings, with evidence |
| Hidden from recipient | SGD account and new FD are not in Priya's post-release grant |
| Unrelated second tenant | Mehta family, one savings account |
| Real-bank utterance mapping | "SBI" → Sagar Bharat Bank (synthetic) in `institution_aliases` |
