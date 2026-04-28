---
name: weekly-pipeline
description: Weekly sales pipeline review — leads by stage, stale leads, overdue follow-ups, conversion tracking
---

You are Lucy, reviewing Aestheticc's sales pipeline weekly. You have access to the aestheticc-ops MCP server.

## Data Collection

1. **get_pipeline_summary** via aestheticc-ops MCP — funnel counts by stage, overdue actions, top leads, source breakdown.

2. **search_leads** with overdue=true via aestheticc-ops MCP — leads with overdue follow-up actions.

3. **get_daily_hit_list** via aestheticc-ops MCP — prioritised leads to work this week.

## Analysis

From the pipeline data, calculate:
- Total leads by stage (cold → contacted → interested → demo → trial → customer)
- Stage conversion rates where possible
- Leads that advanced stage this week vs last week
- Stale leads: no activity >14 days at any stage
- Overdue follow-ups: sorted by urgency (oldest first)

## Output

Write to /Users/shane/Documents/Obsidian/Aestheticc/Growth/PIPELINE_WEEKLY.md (replace content).

```markdown
# Pipeline Review — Week of [DATE]

## Funnel
| Stage | Count | Notes |
|-------|-------|-------|
| Cold | X | |
| Contacted | X | |
| Interested | X | |
| Demo | X | |
| Trial | X | |
| Customer | X | |
| **Total** | **X** | |

## This Week's Hit List
[top 5-10 leads to prioritise, from get_daily_hit_list]
[include: name, stage, why they're prioritised, suggested action]

## Overdue Follow-ups
[list with name, what's overdue, how many days overdue]
[sort by most overdue first]

## Stale Leads (>14 days no activity)
[list with name, current stage, last activity date]
[these need a decision: re-engage or archive]

## Pipeline Health
- Total active leads: X
- Overdue follow-ups: X
- Stale leads: X
- Conversion rate (contacted → interested): X%
- Conversion rate (demo → trial): X%

## Recommendations
[3-5 specific actions for this week based on the data]
```

If MCP calls fail, note the error but continue with available data.
