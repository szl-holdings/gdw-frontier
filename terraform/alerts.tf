locals {
  gdw_alerts = {
    GDWHighP95Latency = {
      expr        = "histogram_quantile(0.95, sum by (le) (rate(gdw_request_latency_seconds_bucket[1m]))) > 1.5"
      for         = "2m"
      severity    = "warning"
      summary     = "GDW p95 latency spike"
      description = "p95 latency exceeded 1.5 seconds for 2 minutes."
    }
    GDWHighErrorRate = {
      expr        = "sum(rate(gdw_request_total{status_class!=\"2xx\"}[1m])) / clamp_min(sum(rate(gdw_request_total[1m])), 1e-9) > 0.05"
      for         = "2m"
      severity    = "critical"
      summary     = "GDW error ratio too high"
      description = "Non-2xx responses exceeded 5 percent for 2 minutes."
    }
    GDWMissingReceipts = {
      expr        = "increase(gdw_receipt_total{status=\"missing\"}[1m]) > 0"
      for         = "1m"
      severity    = "critical"
      summary     = "GDW missing receipt detected"
      description = "A modeled decision completed without a local receipt."
    }
    GDWTelemetryAbsent = {
      expr        = "absent(gdw_build_info) or up{job=\"gdw\"} == 0"
      for         = "2m"
      severity    = "critical"
      summary     = "GDW telemetry is absent"
      description = "The GDW target is down or its build marker is missing."
    }
  }
}

resource "grafana_rule_group" "gdw" {
  name             = "GDW alerts"
  folder_uid       = grafana_folder.gdw.uid
  interval_seconds = 20

  dynamic "rule" {
    for_each = local.gdw_alerts
    content {
      name           = rule.key
      condition      = "C"
      for            = rule.value.for
      no_data_state  = "NoData"
      exec_err_state = "Error"
      is_paused      = false

      annotations = {
        summary     = rule.value.summary
        description = rule.value.description
        runbook     = "docs/RUNBOOKS.md"
      }
      labels = {
        service  = "gdw"
        severity = rule.value.severity
      }

      data {
        ref_id         = "A"
        datasource_uid = grafana_data_source.prometheus.uid

        relative_time_range {
          from = 600
          to   = 0
        }

        model = jsonencode({
          datasource = {
            type = "prometheus"
            uid  = grafana_data_source.prometheus.uid
          }
          editorMode    = "code"
          expr          = rule.value.expr
          instant       = true
          intervalMs    = 1000
          legendFormat  = "__auto"
          maxDataPoints = 43200
          range         = false
          refId         = "A"
        })
      }

      data {
        ref_id         = "C"
        datasource_uid = "__expr__"

        relative_time_range {
          from = 0
          to   = 0
        }

        model = jsonencode({
          conditions = [{
            evaluator = {
              params = [0]
              type   = "gt"
            }
            operator = {
              type = "and"
            }
            query = {
              params = ["C"]
            }
            reducer = {
              params = []
              type   = "last"
            }
            type = "query"
          }]
          datasource = {
            type = "__expr__"
            uid  = "__expr__"
          }
          expression    = "A"
          intervalMs    = 1000
          maxDataPoints = 43200
          refId         = "C"
          type          = "threshold"
        })
      }
    }
  }
}
