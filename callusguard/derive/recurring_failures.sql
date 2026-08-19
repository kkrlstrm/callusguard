-- recurring_failures.sql — the query `callus derive --from-cc-logger` runs against a
-- cc-logger database. Also runnable by hand:
--
--   psql "$NEON_CC_LOGGER_URL" -f callusguard/derive/recurring_failures.sql
--
-- It groups FAILED tool calls by (tool_name, normalized error signature) so a
-- pattern that fails over and over surfaces as one row with a count and a sample
-- command. That row is the raw material for a candidate guard rule.
--
-- THE DENOMINATOR (added with rule tiering)
--   The failure count alone cannot tell a broken command from a busy one: 3
--   failures out of 3 attempts and 3 out of 300 produce the same number and mean
--   opposite things. So this query also counts EVERY attempt of the same command
--   shape — successes included — and returns `attempt_count` alongside
--   `fail_count`. `callusguard/core/tiers.py` turns the pair into a tier, and the
--   tier caps how restrictive a rule may be.
--
--   "Command shape" is the clustering key for the denominator, and it is NOT the
--   error signature: a successful call has no error to key on. For Bash it is the
--   first real token (basename, after any leading VAR=val assignments), plus the
--   subcommand for known multiplexers — `git push`, not `git`. For everything else
--   it is the tool, with MCP tools collapsed to their server surface. This mirrors
--   `_first_tokens` / `_tool_group` in rules.py so both input modes cluster alike.
--
--   The join is LEFT: a failure whose shape somehow finds no attempts keeps its
--   row with a NULL attempt_count and lands in the `unknown` tier, rather than
--   vanishing from the review.
--
-- Note: this query returns EVERY failing tool. rules.py then drops read-only /
-- context tools (Read, Skill, Glob, Grep, …) before proposing rules — their
-- failures are ordinary agent behaviour, not rule material. If you run this SQL by
-- hand, expect those rows here; that is the raw view, not the candidate list.
--
-- Normalization collapses digits -> #, quoted strings -> 'S', and whitespace, so
-- "database \"foo\" does not exist" and "database \"bar\" does not exist" cluster
-- together. Tune the interval and the HAVING threshold to taste.

WITH windowed AS (
    SELECT
        tool_name,
        status,
        error,
        started_at,
        tool_input,
        -- Drop leading `VAR=val ` assignments so `PGPASSWORD=x psql …` shapes as `psql`.
        regexp_replace(
            coalesce(tool_input ->> 'command', ''),
            '^\s*([A-Za-z_][A-Za-z0-9_]*=\S*\s+)+', ''
        ) AS cmd_stripped
    FROM tool_calls
    WHERE started_at > now() - interval '7 days'
      -- Only settled calls. A 'pending' or 'orphaned' row is not evidence either
      -- way, and counting it as an attempt would deflate every failure rate.
      AND status IN ('success', 'failure')
),
shaped AS (
    SELECT
        w.*,
        CASE
            WHEN w.tool_name <> 'Bash' THEN
                -- MCP tools collapse to their server surface: mcp__Acme__run_sql
                -- and mcp__Acme__list share one denominator, matching _tool_group.
                CASE WHEN w.tool_name LIKE 'mcp\_\_%'
                     THEN 'mcp__' || split_part(w.tool_name, '__', 2) || '__'
                     ELSE w.tool_name END
            ELSE
                CASE
                    WHEN head.tok IN ('git','npm','pnpm','yarn','docker','kubectl',
                                      'cargo','go','pip','pip3','python','python3',
                                      'psql','aws','gcloud','make','brew','apt',
                                      'apt-get','systemctl','launchctl')
                         AND coalesce(head.tok2, '') <> ''
                         AND left(head.tok2, 1) <> '-'
                    THEN head.tok || ' ' || head.tok2
                    ELSE head.tok
                END
        END AS command_shape
    FROM windowed w
    CROSS JOIN LATERAL (
        SELECT
            -- basename of the first token: /usr/bin/psql -> psql
            regexp_replace(
                coalesce((regexp_split_to_array(btrim(w.cmd_stripped), '\s+'))[1], ''),
                '^.*/', '') AS tok,
            (regexp_split_to_array(btrim(w.cmd_stripped), '\s+'))[2] AS tok2
    ) head
),
attempts AS (
    -- The denominator: every settled call of this shape, whatever its outcome.
    SELECT command_shape, count(*) AS attempt_count
    FROM shaped
    WHERE command_shape IS NOT NULL AND command_shape <> ''
    GROUP BY 1
),
failures AS (
    SELECT
        tool_name,
        command_shape,
        regexp_replace(
            regexp_replace(
                regexp_replace(lower(coalesce(error, '')), '''[^'']*''|"[^"]*"', '''S''', 'g'),
                '[0-9]+', '#', 'g'),
            '\s+', ' ', 'g'
        )                                            AS error_signature,
        count(*)                                     AS fail_count,
        min(started_at)                              AS first_seen,
        max(started_at)                              AS last_seen,
        (array_agg(tool_input ->> 'command'
                   ORDER BY started_at DESC)
           FILTER (WHERE tool_input ? 'command'))[1] AS sample_command,
        (array_agg(left(coalesce(error, ''), 300)
                   ORDER BY started_at DESC))[1]     AS sample_error
    FROM shaped
    WHERE status = 'failure'
    GROUP BY 1, 2, 3
    -- Bash thresholds here, per signature. Everything else must NOT: rules.py
    -- aggregates non-Bash tools into one tool-wide candidate and thresholds on
    -- that total, so a surface failing 1x across four signatures is a 4x signal it
    -- is supposed to catch. Filtering those rows out here deleted them before the
    -- aggregation could see them, and the JSONL path (which returns every cluster
    -- unthresholded) had been quietly finding tool-wide signals this one could not.
    -- The two input modes are meant to agree.
    HAVING count(*) >= 3 OR tool_name <> 'Bash'
)
SELECT
    f.tool_name,
    f.error_signature,
    f.fail_count,
    a.attempt_count,
    f.command_shape,
    f.first_seen,
    f.last_seen,
    f.sample_command,
    f.sample_error
FROM failures f
LEFT JOIN attempts a USING (command_shape)
ORDER BY f.fail_count DESC, f.tool_name;
