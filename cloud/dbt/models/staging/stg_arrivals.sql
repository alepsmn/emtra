{% if lake_has_files('emtra.arrivals.v1') %}
with env as ({{ lake_envelopes('emtra.arrivals.v1') }})
select
    cast(msg_id as uuid) as msg_id,
    cast(event_time as timestamptz) as event_time,
    cast(payload.line_id as varchar) as line_id,
    cast(payload.direction_id as integer) as direction_id,
    cast(payload.stop_id as varchar) as stop_id,
    cast(payload.stop_sequence as integer) as stop_sequence,
    cast(payload.bus_id as varchar) as bus_id,
    cast(payload.arrival_time as timestamptz) as arrival_time,
    cast(payload.uncertainty_s as double) as uncertainty_s,
    cast(payload.last_seen_at as timestamptz) as last_seen_at,
    cast(payload.first_missing_at as timestamptz) as first_missing_at
from env
{% else %}
select
    cast(null as uuid) as msg_id, cast(null as timestamptz) as event_time,
    cast(null as varchar) as line_id, cast(null as integer) as direction_id,
    cast(null as varchar) as stop_id, cast(null as integer) as stop_sequence,
    cast(null as varchar) as bus_id, cast(null as timestamptz) as arrival_time,
    cast(null as double) as uncertainty_s, cast(null as timestamptz) as last_seen_at,
    cast(null as timestamptz) as first_missing_at
where false
{% endif %}
