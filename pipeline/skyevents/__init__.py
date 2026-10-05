"""Sky-events aggregation pipeline for Skywatch.

Fetches showers, comets, aurora forecasts, eclipses and news from public
sources, normalises them to one event schema, deduplicates, and keeps a
reviewable drafts file from which the approved feed is published.
See EVENTS_AND_NOTIFICATIONS.md in the game repo, section 1.4.
"""
