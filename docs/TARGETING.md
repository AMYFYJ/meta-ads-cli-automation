# Ad Set Audience Targeting — Workbook Reference

How to specify a target audience per ad set: everything lives directly on that
ad set's row in the `AdSets` tab. The pipeline turns these columns into the
Graph API `targeting` payload when the ad set is created (`ads_agent launch` /
`apply-approved`, or `meta_ads_pipeline apply`).

`targeting_json` (raw Graph JSON) merges over the columns and always wins;
lists merge with de-duplication, scalar values (ages, etc.) are overridden.

## Targeting columns on `AdSets`

| Column | Format | Example |
| --- | --- | --- |
| `countries` | 2-letter ISO codes, comma-separated (UK is `GB`) | `US, CA` |
| `regions` | Meta region keys | `3847` (California) |
| `cities` | Meta city keys | `2418779` (Boston) |
| `zips` | `CC:zip` keys | `US:02110, US:02111` |
| `age_min` / `age_max` | 13–65 (65 = 65+) | `25` / `54` |
| `genders` | `all`, `male`, `female` | `female` |
| `languages` | Meta locale codes | `6` (English US), `24` |
| `interests` | `<id>:<Name>` pairs, comma-separated (OR'd) | `6003306084421:Yoga, 6003384248805:Fitness and wellness` |
| `behaviors` | `<id>:<Name>` pairs | `6002714895372:Frequent travelers` |
| `flexible_spec_json` | JSON list of detailed-targeting groups — groups are AND'd, entries within a group are OR'd | see below |
| `exclusions_json` | JSON object of detailed targeting to exclude | `{"interests": [{"id": "6003397496347"}]}` |
| `custom_audiences` | `audience_key`s from the Audiences sheet, or numeric Meta audience IDs | `aud_site_visitors_30d, 23851234567890123` |
| `excluded_audiences` | same | `aud_purchasers_180d` |
| `placements` | `automatic`, or publisher platforms | `facebook, instagram` |
| `facebook_positions` / `instagram_positions` / `device_platforms` | manual placement detail | `feed, marketplace` |
| `advantage_audience`, `detailed_targeting_expansion`, `custom_audience_expansion`, `advantage_placements` | Advantage+ toggles, TRUE/FALSE | `TRUE` |
| `targeting_json` | full/partial raw Graph targeting JSON, merged last | `{"geo_locations": {"location_types": ["home"]}}` |

Notes:

- **Names are cosmetic, IDs are required.** `interests`/`behaviors` entries must
  start with the numeric Meta targeting ID; the part after `:` is a readable
  label sent along to Meta. Validation blocks name-only entries.
- **Custom audiences chain automatically.** Reference an `audience_key` whose
  audience has no `meta_audience_id` yet and the ad set create will wait for
  (and use the ID from) that audience's create in the same apply. The audience
  row must be `APPROVED` or the ad set is skipped with a clear message.
- Any targeting beyond bare `countries` routes the ad set create through the
  Graph API automatically — age/gender/interest columns are never silently
  dropped.
- With the `advantage_audience` toggle set to TRUE, interests and audiences act
  as suggestions Meta may expand beyond; `advantage_placements` TRUE strips the
  manual placement columns and lets Meta place automatically.

## Finding targeting IDs

```bash
.venv/bin/python -m meta_ads_pipeline targeting-search --q "yoga"                  # interests
.venv/bin/python -m meta_ads_pipeline targeting-search --q "travel" --kind behavior
# kinds: interest, behavior, demographic, life_event, industry, income, family_status
```

Prints matching IDs, names, audience sizes, and a ready-to-paste
`<id>:<Name>, ...` line for the workbook cell. Uses `ACCESS_TOKEN` from `.env`.
Region/city/zip keys: use Ads Manager's location typeahead or Graph
`/search?type=adgeolocation` — IDs, not names, go in the columns.

## `flexible_spec_json` example

"(Yoga OR Fitness) AND (Frequent travelers)":

```json
[
  {"interests": [{"id": "6003306084421", "name": "Yoga"}, {"id": "6003384248805", "name": "Fitness and wellness"}]},
  {"behaviors": [{"id": "6002714895372", "name": "Frequent travelers"}]}
]
```

## Migrating an older workbook

Workbooks from before the tab consolidation (separate TargetingPresets,
AutomationSettings, Creatives, AudienceUploads, DuplicateJobs tabs) can be
upgraded in place — presets and automation fold into the AdSets columns,
creatives fold into their Ads rows, uploads fold into Audiences, and pending
duplicate jobs become BulkChanges rows (archive a copy first; close Excel):

```bash
.venv/bin/python -m meta_ads_pipeline migrate --source data/current.xlsx --out-source data/current.xlsx
```

`data/current.xlsx` was migrated to the consolidated 10-tab layout on 2026-07-14.
