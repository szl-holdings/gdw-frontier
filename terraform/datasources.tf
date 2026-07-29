resource "grafana_data_source" "prometheus" {
  type       = "prometheus"
  name       = "GDW Prometheus"
  uid        = "gdw-prometheus"
  url        = "http://prometheus:9090"
  is_default = true

  json_data_encoded = jsonencode({
    httpMethod   = "POST"
    timeInterval = "5s"
    exemplarTraceIdDestinations = [{
      name          = "trace_id"
      datasourceUid = "gdw-tempo"
    }]
  })
}

resource "grafana_data_source" "loki" {
  type = "loki"
  name = "GDW Loki"
  uid  = "gdw-loki"
  url  = "http://loki:3100"

  json_data_encoded = jsonencode({
    derivedFields = [{
      name          = "TraceID"
      matcherRegex  = "\"trace_id\":\"([a-f0-9]{32})\""
      datasourceUid = "gdw-tempo"
      url           = "$${__value.raw}"
    }]
    maxLines = 1000
  })
}

resource "grafana_data_source" "tempo" {
  type = "tempo"
  name = "GDW Tempo"
  uid  = "gdw-tempo"
  url  = "http://tempo:3200"

  json_data_encoded = jsonencode({
    tracesToLogsV2 = {
      datasourceUid      = "gdw-loki"
      spanStartTimeShift = "-5m"
      spanEndTimeShift   = "5m"
      filterByTraceID    = true
      filterBySpanID     = false
    }
    tracesToMetrics = {
      datasourceUid      = "gdw-prometheus"
      spanStartTimeShift = "-5m"
      spanEndTimeShift   = "5m"
    }
    serviceMap = {
      datasourceUid = "gdw-prometheus"
    }
  })
}

