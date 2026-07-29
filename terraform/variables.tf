variable "grafana_url" {
  description = "Grafana base URL."
  type        = string
}

variable "grafana_auth" {
  description = "Grafana service-account token or local admin basic auth."
  type        = string
  sensitive   = true
}

