CREATE TABLE band_status (
    line_id      text NOT NULL,
    direction_id integer NOT NULL,
    segment_id   integer NOT NULL,
    band_start   timestamptz NOT NULL,
    indicator    text NOT NULL CHECK (indicator IN ('cvh', 'otp')),
    value        double precision,
    n_obs        integer NOT NULL,
    status       text NOT NULL CHECK (status IN ('inside', 'warning', 'outside', 'insufficient_data')),
    los          text CHECK (los IN ('A', 'B', 'C', 'D', 'E', 'F')),
    PRIMARY KEY (line_id, direction_id, segment_id, band_start)
);

CREATE TABLE alerts (
    alert_id          uuid PRIMARY KEY,
    line_id           text NOT NULL,
    direction_id      integer NOT NULL,
    segment_id        integer NOT NULL,
    state             text NOT NULL CHECK (state IN ('open', 'closed')),
    level             text NOT NULL CHECK (level IN ('warning', 'action_required')),
    triggers          text[] NOT NULL,
    distance_to_range double precision NOT NULL,
    ewma_w            double precision NOT NULL,
    cusum_s           double precision NOT NULL,
    opened_at         timestamptz NOT NULL,
    updated_at        timestamptz NOT NULL,
    closed_at         timestamptz
);
-- at most one open alert per (line, direction, segment)
CREATE UNIQUE INDEX alerts_one_open ON alerts (line_id, direction_id, segment_id)
    WHERE state = 'open';

CREATE TABLE recommendations (
    recommendation_id uuid PRIMARY KEY,
    alert_id          uuid NOT NULL,
    event_time        timestamptz NOT NULL,
    fallback          boolean NOT NULL,
    runtime_s         double precision NOT NULL,
    replications      integer NOT NULL,
    candidates        jsonb NOT NULL
);
CREATE INDEX recommendations_alert ON recommendations (alert_id);

CREATE TABLE decisions (
    decision_id       uuid PRIMARY KEY,
    alert_id          uuid NOT NULL,
    recommendation_id uuid NOT NULL,
    choice            text NOT NULL,          -- rank, or 'declined'
    action            text NOT NULL,
    params            jsonb NOT NULL,
    operator_id       text NOT NULL,
    decided_at        timestamptz NOT NULL
);
CREATE INDEX decisions_alert ON decisions (alert_id);

CREATE TABLE recoveries (
    decision_id       uuid PRIMARY KEY,
    alert_id          uuid NOT NULL,
    event_time        timestamptz NOT NULL,
    status            text NOT NULL CHECK (status IN ('recovered', 'not_recovered_within_horizon')),
    recovered_at      timestamptz,
    recovery_time_s   double precision,
    peak_deviation    double precision NOT NULL,
    damping_sim_mean  double precision,
    damping_sim_ci95  double precision[]
);
CREATE INDEX recoveries_alert ON recoveries (alert_id);
