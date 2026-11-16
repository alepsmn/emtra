{% if lake_has_files('emtra.alerts.v1') %}
with env as ({{ lake_envelopes('emtra.alerts.v1') }})
select
    cast(msg_id as uuid) as msg_id,
    cast(event_time as timestamptz) as event_time,
    cast(payload.alert_id as uuid) as alert_id,
    cast(payload.line_id as varchar) as line_id,
    cast(payload.direction_id as integer) as direction_id,
    cast(payload.segment_id as integer) as segment_id,
    cast(payload.state as varchar) as state,
    cast(payload.level as varchar) as level,
    cast(payload.triggers as varchar[]) as triggers,
    cast(payload.distance_to_range as double) as distance_to_range,
    cast(payload.ewma_w as double) as ewma_w,
    cast(payload.cusum_s as double) as cusum_s,
    cast(payload.opened_at as timestamptz) as opened_at,
    cast(payload.updated_at as timestamptz) as updated_at,
    try_cast(payload.closed_at as timestamptz) as closed_at
from env
{% else %}
select
    cast(null as uuid) as msg_id, cast(null as timestamptz) as event_time,
    cast(null as uuid) as alert_id, cast(null as varchar) as line_id,
    cast(null as integer) as direction_id, cast(null as integer) as segment_id,
    cast(null as varchar) as state, cast(null as varchar) as level,
    cast(null as varchar[]) as triggers, cast(null as double) as distance_to_range,
    cast(null as double) as ewma_w, cast(null as double) as cusum_s,
    cast(null as timestamptz) as opened_at, cast(null as timestamptz) as updated_at,
    cast(null as timestamptz) as closed_at
where false
{% endif %}
