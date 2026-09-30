terraform {
  required_version = ">= 1.5"

  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.30"
    }
  }
}

# Sin conexión a ningún clúster: el host es inalcanzable a propósito para que ni `validate` ni `plan` puedan tocar un
# clúster real ni leer tu kubeconfig. Este ejemplo solo demuestra la edición del código.
provider "kubernetes" {
  host     = "https://127.0.0.1:1"
  insecure = true
}
