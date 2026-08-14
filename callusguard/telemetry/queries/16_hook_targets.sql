-- 16_hook_targets.sql — "where should the next red squiggly go?"
--
-- A monthly dashboard for monitoring hook opportunities from real telemetry.
-- Sections A–C track the THREE hooks already shipped (PreToolUse guard:
-- scripts/hooks/pretooluse-guard.py) so you can watch their target patterns
-- shrink. Sections D–G are GENERATIVE — they surface NEW recurring failure
-- clusters, helper-bypass drift, unreliable tools, and generated-file edits
-- that are candidates for the next hook.
--
-- A hook is worth adding when a pattern is (1) RECURRING [these queries],
-- (2) DETERMINISTICALLY DETECTABLE from tool_name/tool_input, and (3) has a
-- known fix. These queries find (1); you judge (2) and (3).
--
-- Run against the cc-logger DB (NEON_CC_LOGGER_URL in cc-logger/.env):
--   cd /path/to/your/project
--   set -a; . cc-logger/.env; set +a
--   scripts/neon-psql.sh "$NEON_CC_LOGGER_URL" -f cc-logger/queries/16_hook_targets.sql
--
-- Tune the window / clustering threshold here:
\set window_days 30
\set min_count 3
\pset pager off

\echo '\n================ CORPUS (last :window_days days) ================'
SELECT count(DISTINCT session_id) AS sessions,
       count(*)                    AS tool_calls,
       min(started_at)::date       AS since,
       max(started_at)::date       AS latest
FROM tool_calls
WHERE started_at > now() - (:window_days * interval '1 day');

\echo '\n===== A. HOOK #1 — raw psql heading for the local socket ====='
\echo '(should trend to ~0 as the nudge lands; watch fails especially)'
WITH b AS (
  SELECT tool_input->>'command' AS cmd, status
  FROM tool_calls
  WHERE tool_name = 'Bash'
    AND tool_input ? 'command'
    AND started_at > now() - (:window_days * interval '1 day')
)
SELECT
  count(*) FILTER (WHERE is_psql)                          AS psql_calls,
  count(*) FILTER (WHERE is_psql AND NOT guarded)          AS unguarded_psql,
  count(*) FILTER (WHERE is_psql AND NOT guarded
                        AND status='failure')              AS unguarded_psql_fails
FROM (
  SELECT status,
    cmd ~ '(^|[;|&(]|\$\()[[:space:]]*([A-Za-z_][A-Za-z0-9_]*=[^[:space:]]+[[:space:]]+)*psql([[:space:]]|$)' AS is_psql,
    ( cmd ~ 'neon-psql'
      OR cmd ~ 'postgres(ql)?://'
      OR cmd ~ 'PG(HOST|DATABASE|PASSWORD|USER|PORT)[[:space:]]*='
      OR cmd ~ '(^|[[:space:]])(-h|--host)([[:space:]]|=)' ) AS guarded
  FROM b
) t;

\echo '\n===== B. HOOK #2 — shell-loading .env (the & breakage) ====='
SELECT
  count(*)                                   AS env_load_calls,
  count(*) FILTER (WHERE status='failure')   AS env_load_fails
FROM tool_calls
WHERE tool_name = 'Bash'
  AND started_at > now() - (:window_days * interval '1 day')
  AND ( tool_input->>'command' ~ '(^|[;&|])[[:space:]]*(source|\.)[[:space:]]+[^[:space:];|&]*\.env'
     OR tool_input->>'command' ~ 'export[[:space:]]+\$\([^)]*\.env'
     OR tool_input->>'command' ~ '\.env[^|]*\|[[:space:]]*xargs' );

\echo '\n===== C. HOOK #3 — Neon MCP calls (should be 0 after removal + deny) ====='
SELECT tool_name,
       count(*) AS calls,
       count(*) FILTER (WHERE status='failure') AS fails
FROM tool_calls
WHERE tool_name LIKE 'mcp__Neon__%'
  AND started_at > now() - (:window_days * interval '1 day')
GROUP BY 1 ORDER BY calls DESC;

\echo '\n===== D. NEW HOOK CANDIDATES — recurring Bash failure clusters ====='
\echo '(normalized: digits->N, paths->/…  | each cluster >= :min_count is a candidate)'
SELECT count(*) AS n,
       left(regexp_replace(
              regexp_replace(error, '/[^[:space:]]+', '/…', 'g'),
              '[0-9]+', 'N', 'g'), 130) AS error_cluster
FROM tool_calls
WHERE tool_name = 'Bash' AND status = 'failure' AND error IS NOT NULL
  AND started_at > now() - (:window_days * interval '1 day')
GROUP BY 2
HAVING count(*) >= :min_count
ORDER BY n DESC
LIMIT 25;

\echo '\n===== E. HELPER-BYPASS DRIFT — CLI used directly vs via the helper ====='
\echo '(big raw:helper ratios = candidate for a SessionStart reminder, not a per-call hook)'
WITH b AS (
  SELECT tool_input->>'command' AS cmd
  FROM tool_calls
  WHERE tool_name='Bash' AND tool_input ? 'command'
    AND started_at > now() - (:window_days * interval '1 day')
)
SELECT
  count(*) FILTER (WHERE cmd ~ '(^|[^[:alnum:]_/-])psql ' AND cmd !~ 'neon-psql') AS raw_psql,
  count(*) FILTER (WHERE cmd ~ 'neon-psql')                                       AS via_neon_psql,
  count(*) FILTER (WHERE cmd ~ 'glab api')                                        AS raw_glab_api,
  count(*) FILTER (WHERE cmd ~ 'gl\.py')                                          AS via_gl_py,
  count(*) FILTER (WHERE cmd ~ 'bison-report')                                    AS via_bison_report,
  count(*) FILTER (WHERE cmd ~ 'python3? +-c')                                    AS python_dash_c,
  count(*) FILTER (WHERE cmd ~ '<<[-~]?[[:space:]]*''?[A-Z]')                     AS heredocs
FROM b;

\echo '\n===== F. UNRELIABLE TOOLS / NEW MCP TRAPS (fail % leaderboard) ====='
\echo '(a freshly-appearing mcp__ server with high fail % = the next #3-style deny)'
SELECT tool_name,
       count(*) AS total,
       count(*) FILTER (WHERE status='failure') AS fails,
       round(100.0*count(*) FILTER (WHERE status='failure')/NULLIF(count(*),0),1) AS fail_pct
FROM tool_calls
WHERE started_at > now() - (:window_days * interval '1 day')
GROUP BY 1
HAVING count(*) >= 5 AND count(*) FILTER (WHERE status='failure') > 0
ORDER BY fail_pct DESC, total DESC
LIMIT 20;

\echo '\n===== G. GENERATED / AUTO-FILE EDITS (candidate hook #4) ====='
SELECT
  count(*) FILTER (WHERE fp ~ 'config/clients\.json')      AS clients_json,
  count(*) FILTER (WHERE fp ~ '\.claude/skills/client-')   AS client_skill_md,
  count(*) FILTER (WHERE fp ~ 'INDEX\.md$')                AS index_md
FROM (
  SELECT tool_input->>'file_path' AS fp
  FROM tool_calls
  WHERE tool_name IN ('Edit','Write') AND tool_input ? 'file_path'
    AND started_at > now() - (:window_days * interval '1 day')
) f;

\echo '\n===== H. EDIT/WRITE HEALTH (confirms no "red-squiggly-for-edits" need) ====='
SELECT tool_name,
       count(*) AS total,
       count(*) FILTER (WHERE status='failure') AS fails,
       round(100.0*count(*) FILTER (WHERE status='failure')/NULLIF(count(*),0),1) AS fail_pct
FROM tool_calls
WHERE tool_name IN ('Edit','Write','NotebookEdit')
  AND started_at > now() - (:window_days * interval '1 day')
GROUP BY 1 ORDER BY total DESC;
