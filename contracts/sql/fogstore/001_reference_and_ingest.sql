CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE stops (
    stop_id       text PRIMARY KEY,
    name          text NOT NULL,
    lat           double precision NOT NULL,
    lon           double precision NOT NULL,
    stop_sequence integer NOT NULL UNIQUE,
    segment_id    integer NOT NULL
);

CREATE TABLE schedule (
    line_id       text NOT NULL,
    direction_id  integer NOT NULL,
    trip_id       text NOT NULL,
    stop_id       text NOT NULL REFERENCES stops,
    stop_sequence integer NOT NULL,
    sched_arrival timestamptz NOT NULL,
    PRIMARY KEY (trip_id, stop_id)
);
CREATE INDEX schedule_line_time ON schedule (line_id, direction_id, stop_id, sched_arrival);

CREATE TABLE estimates (
    polled_at    timestamptz NOT NULL,
    stop_id      text NOT NULL,
    line_id      text NOT NULL,
    direction_id integer NOT NULL,
    bus_id       text NOT NULL,
    time_left_s  integer NOT NULL,
    distance_m   integer NOT NULL
);
SELECT create_hypertable('estimates', 'polled_at');
CREATE INDEX estimates_stop_time ON estimates (stop_id, polled_at DESC);

-- a hypertable unique index must contain the partition column; arrival_time is a function of
-- the natural key behind msg_id, so (msg_id, arrival_time) still deduplicates.
CREATE TABLE arrivals (
    msg_id           uuid NOT NULL,
    line_id          text NOT NULL,
    direction_id     integer NOT NULL,
    stop_id          text NOT NULL,
    stop_sequence    integer NOT NULL,
    bus_id           text NOT NULL,
    arrival_time     timestamptz NOT NULL,
    uncertainty_s    double precision NOT NULL,
    last_seen_at     timestamptz NOT NULL,
    first_missing_at timestamptz NOT NULL,
    UNIQUE (msg_id, arrival_time)
);
SELECT create_hypertable('arrivals', 'arrival_time');
CREATE INDEX arrivals_line_time ON arrivals (line_id, direction_id, stop_id, arrival_time);

CREATE TABLE never_fulfilled (
    msg_id           uuid PRIMARY KEY,
    line_id          text NOT NULL,
    direction_id     integer NOT NULL,
    stop_id          text NOT NULL,
    bus_id           text NOT NULL,
    last_seen_at     timestamptz NOT NULL,
    last_time_left_s integer NOT NULL
);
