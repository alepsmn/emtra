CREATE VIEW v_line_status_latest AS
SELECT DISTINCT ON (line_id, direction_id, segment_id) *
FROM band_status
ORDER BY line_id, direction_id, segment_id, band_start DESC;

CREATE VIEW v_open_alerts AS
SELECT * FROM alerts WHERE state = 'open';

-- alert -> recommendation -> decision -> recovery, joined by alert_id
CREATE VIEW v_alert_timeline AS
SELECT a.alert_id, a.line_id, a.direction_id, a.segment_id, a.state, a.level,
       a.opened_at, a.closed_at,
       r.recommendation_id, r.event_time AS recommended_at, r.fallback,
       d.decision_id, d.decided_at, d.choice, d.action,
       v.status AS recovery_status, v.recovered_at, v.recovery_time_s,
       v.damping_sim_mean
FROM alerts a
LEFT JOIN recommendations r USING (alert_id)
LEFT JOIN decisions d USING (alert_id)
LEFT JOIN recoveries v USING (alert_id);
