{# Files of one fog->cloud topic in the local lake (see services/cloud_sink). #}
{% macro lake_files(topic) -%}
{{ var('lake_dir') }}/{{ topic }}/dt=*/*.ndjson.gz
{%- endmacro %}

{# read_json_auto fails when the glob matches nothing (a topic with no messages yet, e.g. no
   alerts): staging then falls back to an empty relation with the same columns. #}
{% macro lake_has_files(topic) -%}
{%- if execute -%}
  {%- set r = run_query("select count(*) from glob('" ~ lake_files(topic) ~ "')") -%}
  {{- return(r.columns[0].values()[0] > 0) -}}
{%- else -%}
  {{- return(true) -}}
{%- endif -%}
{%- endmacro %}

{# Envelopes of a topic, one row per msg_id: the bridge is at-least-once, so a crash can leave
   repeated messages in Kafka and in the lake (decision 1 of WP7). #}
{% macro lake_envelopes(topic) -%}
select * from read_json_auto('{{ lake_files(topic) }}', format = 'newline_delimited')
qualify row_number() over (partition by msg_id order by produced_at) = 1
{%- endmacro %}

{# Generic test: no two rows share the same values of `columns`. #}
{% test unique_combination(model, columns) %}
select {{ columns | join(', ') }}, count(*) as n
from {{ model }}
group by {{ columns | join(', ') }}
having count(*) > 1
{% endtest %}
