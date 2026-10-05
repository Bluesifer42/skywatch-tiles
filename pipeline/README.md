# Sky events pipeline

Aggregates what is happening in the sky from public sources into one feed the
Skywatch app reads. Design: `EVENTS_AND_NOTIFICATIONS.md` in the game repo.

```
sources -> fetchers -> normalise -> dedupe -> events/drafts.json
                                                  |
                                   pull request opened by the bot
                                                  |
                                  reviewer approves / rejects, merges
                                                  |
                           events/events.json + events/events-lite.json
```

## Sources

| Fetcher | Source | Auto-approved |
|---|---|---|
| showers | Static IAU table, peak refined from the IMO calendar when it parses | yes |
| comets | Minor Planet Center observable-comet elements, two-body ephemeris, brightest observable moment in the next 90 days | no |
| aurora | NOAA SWPC planetary Kp forecast, Kp 5 or more | yes |
| eclipses | Static NASA table to 2030 | yes |
| news | NASA, ESA, EarthSky, Universe Today RSS; headline, one line, link (Sky & Telescope blocks bots) | no |
| apod | NASA picture of the day (`NASA_API_KEY` secret, else DEMO_KEY) | no |

Facts are stored with attribution; prose and images are linked, not copied.

## Commands

```
python pipeline/fetch_events.py fetch            # refresh drafts
python pipeline/fetch_events.py list --status draft
python pipeline/fetch_events.py approve <id>     # or reject <id>
python pipeline/fetch_events.py publish          # write the feeds
python -m unittest discover -s pipeline/tests    # offline, uses fixtures
```

`SKYEVENTS_FIXTURES=<dir>` makes the fetchers read saved responses instead
of the network.

## Review

Every six hours the fetch workflow refreshes `events/drafts.json` and opens or
updates one pull request. Review it, flip `status` to `approved` or `rejected`
where needed, merge. Merging publishes the feeds. Rejected and expired records
stay in the file so the bot does not resurrect them. Approved records are
frozen: later fetches add sources but do not change the words.

Feed URLs on GitHub Pages:

- https://bluesifer42.github.io/skywatch-tiles/events/events-lite.json (next 60 days, what phones fetch)
- https://bluesifer42.github.io/skywatch-tiles/events/events.json
