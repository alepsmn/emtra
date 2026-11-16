-- One row per estimate (a snapshot carries up to 2 buses per line); a poll with no estimates
-- yields no row. Unique on (msg_id, estimate_pos), not on msg_id.
{% if lake_has_files('emtra.snapshots.v1') %}
with env as ({{ lake_envelopes('emtra.snapshots.v1') }})
select
    cast(msg_id as uuid) as msg_id,
    p.pos as estimate_pos,
    cast(event_time as timestamptz) as event_time,
    cast(payload.stop_id as varchar) as stop_id,
    cast(payload.polled_at as timestamptz) as polled_at,
    cast(payload.arrivals[p.pos].line_id as varchar) as line_id,
    cast(payload.arrivals[p.pos].direction_id as integer) as direction_id,
    cast(payload.arrivals[p.pos].bus_id as varchar) as bus_id,
    cast(payload.arrivals[p.pos].time_left_s as integer) as time_left_s,
    cast(payload.arrivals[p.pos].distance_m as integer) as distance_m
from env, unnest(range(1, len(payload.arrivals) + 1)) as p(pos)
{% else %}
select
    cast(null as uuid) as msg_id, cast(null as bigint) as estimate_pos,
    cast(null as timestamptz) as event_time, cast(null as varchar) as stop_id,
    cast(null as timestamptz) as polled_at, cast(null as varchar) as line_id,
    cast(null as integer) as direction_id, cast(null as varchar) as bus_id,
    cast(null as integer) as time_left_s, cast(null as integer) as distance_m
where false
{% endif %}
