{% if lake_has_files('emtra.status.v1') %}
with env as ({{ lake_envelopes('emtra.status.v1') }})
select
    cast(msg_id as uuid) as msg_id,
    cast(event_time as timestamptz) as event_time,
    cast(payload.line_id as varchar) as line_id,
    cast(payload.direction_id as integer) as direction_id,
    cast(payload.segment_id as integer) as segment_id,
    cast(payload.band_start as timestamptz) as band_start,
    cast(payload.indicator as varchar) as indicator,
    try_cast(payload.value as double) as value,
    cast(payload.n_obs as integer) as n_obs,
    cast(payload.status as varchar) as status,
    try_cast(payload.los as varchar) as los
from env
{% else %}
select
    cast(null as uuid) as msg_id, cast(null as timestamptz) as event_time,
    cast(null as varchar) as line_id, cast(null as integer) as direction_id,
    cast(null as integer) as segment_id, cast(null as timestamptz) as band_start,
    cast(null as varchar) as indicator, cast(null as double) as value,
    cast(null as integer) as n_obs, cast(null as varchar) as status, cast(null as varchar) as los
where false
{% endif %}
