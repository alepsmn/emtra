CREATE TABLE calibration_cells (
    id           bigserial PRIMARY KEY,
    version      integer NOT NULL,
    valid_from   timestamptz NOT NULL,
    alpha        double precision NOT NULL,
    line_id      text,                    -- NULL = all lines
    horizon_band text NOT NULL,
    band_group   text,                    -- NULL = all groups
    q_lo_s       double precision NOT NULL,
    q_hi_s       double precision NOT NULL,
    n            integer NOT NULL
);
CREATE INDEX calibration_cells_version ON calibration_cells (version);

CREATE TABLE service_health (
    service    text PRIMARY KEY,
    status     text NOT NULL,
    updated_at timestamptz NOT NULL,
    metrics    jsonb NOT NULL DEFAULT '{}'
);
