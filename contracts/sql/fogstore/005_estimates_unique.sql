-- Idempotent estimate inserts (DEVIATIONS 23). The unique index contains the hypertable
-- partition column (polled_at); direction_id is a function of line_id.
CREATE UNIQUE INDEX estimates_natural_key ON estimates (stop_id, line_id, bus_id, polled_at);
