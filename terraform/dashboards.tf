resource "grafana_folder" "gdw" {
  title = "GDW"
  uid   = "gdw-observability"
}

resource "grafana_dashboard" "gdw_live_ops" {
  folder      = grafana_folder.gdw.uid
  config_json = file("${path.module}/../monitoring/grafana/dashboards/gdw_live_ops_dashboard.json")
  overwrite   = true

  depends_on = [
    grafana_data_source.prometheus,
    grafana_data_source.loki,
    grafana_data_source.tempo,
  ]
}

