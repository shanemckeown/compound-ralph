---
name: help-insights
description: Weekly report on what users search for in the help system — feedback loop for UX improvement
---

You are Lucy, Aestheticc's AI operations assistant. Produce a weekly help system insights report.

Your working directory is /Users/shane/Documents/Obsidian/Aestheticc.

## Data Collection

Query PostHog via their HogQL API to get help system usage from the past 7 days.

**API details:**
- Endpoint: `https://eu.i.posthog.com/api/projects/@current/query/`
- Method: POST
- Auth header: `Authorization: Bearer $POSTHOG_PERSONAL_API_KEY` (export from `~/.claude/.env`, NEVER hardcode — GitHub push protection will block)
- Content-Type: application/json
- ALWAYS use `dangerouslyDisableSandbox: true` for these requests

### Query 1: Event totals (overall usage)
```json
{
  "query": {
    "kind": "HogQLQuery",
    "query": "SELECT event, count() as count FROM events WHERE event LIKE 'help_%' AND timestamp > now() - interval 7 day GROUP BY event ORDER BY count DESC"
  }
}
```

### Query 2: Top search queries (what people are confused about)
```json
{
  "query": {
    "kind": "HogQLQuery",
    "query": "SELECT properties.$help_query as query, count() as count FROM events WHERE event = 'help_searched' AND timestamp > now() - interval 7 day GROUP BY query ORDER BY count DESC LIMIT 20"
  }
}
```
Note: if `$help_query` returns nothing, try `properties.query` instead (the property name in our code is `query`).

### Query 3: No-results searches (content gaps)
```json
{
  "query": {
    "kind": "HogQLQuery",
    "query": "SELECT properties.query as query, properties.screen_key as screen, count() as count FROM events WHERE event = 'help_no_results' AND timestamp > now() - interval 7 day GROUP BY query, screen ORDER BY count DESC LIMIT 20"
  }
}
```

### Query 4: Most-opened screens (where people need help most)
```json
{
  "query": {
    "kind": "HogQLQuery",
    "query": "SELECT properties.screen_key as screen, count() as count FROM events WHERE event = 'help_opened' AND timestamp > now() - interval 7 day GROUP BY screen ORDER BY count DESC LIMIT 15"
  }
}
```

### Query 5: Video watches (which videos are valuable)
```json
{
  "query": {
    "kind": "HogQLQuery",
    "query": "SELECT properties.title as title, properties.video_id as video_id, count() as count FROM events WHERE event = 'help_video_watched' AND timestamp > now() - interval 7 day GROUP BY title, video_id ORDER BY count DESC LIMIT 10"
  }
}
```

### Query 6: Unique users using help
```json
{
  "query": {
    "kind": "HogQLQuery",
    "query": "SELECT uniq(distinct_id) as unique_users, count() as total_events FROM events WHERE event LIKE 'help_%' AND timestamp > now() - interval 7 day"
  }
}
```

## Report Generation

Write the report to `Ops/HELP_INSIGHTS.md` with this structure:

```markdown
# Help System Insights — Week of {date}

## Summary
- **Unique users:** X users opened help this week
- **Total opens:** X | **Searches:** X | **Videos watched:** X | **No-results:** X

## Top Searches (what people are asking)
| # | Query | Count |
|---|-------|-------|
| 1 | ...   | ...   |

## Content Gaps (searches with no results)
| Query | Screen | Count | Action Needed |
|-------|--------|-------|---------------|
| ...   | ...    | ...   | Add content / Fix UX |

## Screens Where Help Is Opened Most
| Screen | Opens |
|--------|-------|
| ...    | ...   |

## Video Engagement
| Title | Watches |
|-------|---------|
| ...   | ...     |

## Recommendations
Based on the data:
1. [If no-results exist] Add help content for: ...
2. [If a screen dominates opens] Consider UX improvements to: ...
3. [If a search term repeats] This feature may need better discoverability: ...
```

## Edge Cases
- If there are ZERO events (system just launched), write a short note saying the help system is live but no usage data yet. Don't produce empty tables.
- If PostHog API returns errors, log the error and note it in the report rather than failing silently.

## After Writing
Tell Shane what the key findings are in 2-3 bullet points. If there are content gaps (no-results searches), flag them as potential beads to create.
