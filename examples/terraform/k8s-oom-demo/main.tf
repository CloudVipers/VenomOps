# Corresponde al Pod de examples/k8s/20-oomkilled.yaml (falla deliberada KD-K8S-002: límite de memoria demasiado bajo).
resource "kubernetes_pod_v1" "oom" {
  metadata {
    name      = "oom"
    namespace = "venom-demo"
  }

  spec {
    restart_policy = "Always"

    container {
      name    = "app"
      image   = "python:3.12-alpine"
      command = ["python", "-c", "x = bytearray(150 * 1024 * 1024); import time; time.sleep(60)"]

      resources {
        requests = {
          memory = "16Mi"
        }
        limits = {
          memory = "32Mi"
        }
      }
    }
  }
}
