-- recurring_failures.sql — the query bin/derive_rules.py runs against a cc-logger
-- database (https://github.com/…/cc-logger). Also runnable by hand:
--
--   psql "$NEON_CC_LOGGER_URL" -f sql/recurring_failures.sql
--
-- It groups FAILED tool calls by (tool_name, normalized error signature) so a
-- pattern that fails over and over surfaces as one row with a count and a sample
-- command. That row is the raw material for a candidate guard rule.
--
-- Normalization collapses digits -> #, quoted strings -> 'S', and whitespace, so
-- "database \"foo\" does not exist" and "database \"bar\" does not exist" cluster
-- together. Tune the interval and the HAVING threshold to taste.

SELECT
  tool_name,
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
             ORDER BY started_at DESC))[1]      AS sample_error
FROM tool_calls
WHERE status = 'failure'
  AND started_at > now() - interval '7 days'
GROUP BY 1, 2
HAVING count(*) >= 3
ORDER BY fail_count DESC, tool_name;
