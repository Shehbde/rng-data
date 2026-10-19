# rng-data

Live data for **RNG**, the client-side Fabric mod that plays cutscenes on Hypixel SkyBlock rare drops: drop
tables, item display data, chat/scoreboard regexes and signature-ending mappings.

> [!WARNING]
> **Every push to `main` goes live to every player.** Mods check this repository at startup and every few
> hours, and download any change automatically. Edit carefully, run the checks before pushing, and use a
> pull request if you are not sure.

The mod ships with a copy of this data, so it works offline. This repository lets drop rates, new items and
changed chat messages be fixed without a mod release. It contains data only: the mod parses these files as JSON
and never runs anything from them.

## Layout

| path | what it is |
|---|---|
| `bundle/sources.json` | Every activity that can drop something rare (slayer tiers, dungeon floors, Kuudra tiers, sea creatures, ...) with its loot pool, odds and detection hints. |
| `bundle/items.json` | Name, rarity and icon (vanilla material, skull texture, dye colour, glint) for every item referenced anywhere. |
| `bundle/drops.json` | Every rare item with all the sources that drop it. |
| `bundle/patterns.json` | Regexes for chat, scoreboard, tab list, nametags and chest titles. Java regex syntax, run on text with formatting codes removed. Keys and named groups are used by the mod's code. |
| `bundle/signatures.json` | Which items trigger which built-in signature ending. |
| `schema/*.schema.json` | A JSON Schema (draft 2020-12) for every file. The mod has its own copy and checks every download against it. |
| `manifest.json` | Written by the workflow. `bundleVersion`, `minModVersion` and the SHA-256 + size of every file in `bundle/`. Do not edit the `files` or `bundleVersion` fields by hand. |
| `scripts/publish.py` | The checks the workflow runs. Run it yourself before pushing. |
| `.github/workflows/publish.yml` | Validates every push and publishes the manifest. |

## How to edit data

1. Edit the JSON in `bundle/` (`sources.json`, `items.json` and `drops.json` hold one record per line).
   - Odds are a probability: `"chance": 0.0101` is about 1 in 99. Put the human-readable form in `chanceText`.
   - Tiers (`dropTier`, `minTier`) are `COMMON`, `UNCOMMON`, `RARE`, `VERY_RARE`, `CRAZY_RARE`, `PRAY_TO_RNGESUS`,
     `INSANE`, `PET`. Rarities are `COMMON` ... `MYTHIC`, `DIVINE`, `SPECIAL`, `VERY_SPECIAL`, `ULTIMATE`, `ADMIN`.
   - An item used in a loot pool needs an entry in `items.json`. A rare item should also appear in `drops.json`.
   - Set `"verified": true` only when you checked the value against the wiki, and put the page in `wiki`.
   - Regexes in `patterns.json` must keep their key and their named groups (`(?<tier>...)`), because the mod's
     code reads them.
2. Check it locally (Python 3.10+):
   ```sh
   python -m pip install jsonschema
   python scripts/publish.py
   ```
3. Commit and push to `main` (or open a pull request; the same checks run on it).
4. The **Validate and publish data** workflow runs. If any file is invalid the run fails and nothing changes
   for players. If everything passes, it raises `bundleVersion`, rewrites `manifest.json` with the new
   checksums and commits it as `github-actions[bot]` ("Publish data vN [skip ci]").
5. Players get the change at their next launch or within 6 hours (the interval is a setting). Settings →
   General → Data → **Check now** fetches it right away. GitHub's CDN can take up to 5 minutes to serve the new
   files; a check in that window fails safely and is retried.

## What the mod does with it

- Fetches `manifest.json` (HTTPS only, timeouts, 64 KB cap). Skips it if `minModVersion` is newer than the
  installed mod, or if `bundleVersion` is not higher than the data it already has.
- Downloads `bundle/<name>` only for files whose SHA-256 differs from the copy it already has (cached or
  bundled in the jar). Files over 5 MB are refused.
- Verifies size + SHA-256, validates every file against its own copy of the schemas, compiles every regex and
  loads the data. Only then does it switch to the new data and save it as the last good bundle.
- On any failure it keeps what it had: the last good bundle, or the copy bundled in the jar.

## Changing the format

The mod validates downloads against the schemas built into it, not the ones in this repository. A new field, a
new file or a relaxed rule therefore needs a mod release that understands it first. Then update `schema/` here,
raise `minModVersion` in `manifest.json` to that release, and push. Older mods skip data they can't use and keep
working with what they have.

## Never commit secrets

This repository is public and needs no credentials: the workflow uses the built-in `GITHUB_TOKEN` and players
download anonymously. Never add tokens, passwords or `.env` files.
