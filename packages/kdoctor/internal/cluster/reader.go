// Package cluster implements engine.ClusterReader on top of client-go using only get/list calls.
package cluster

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"reflect"

	corev1 "k8s.io/api/core/v1"
	policyv1 "k8s.io/api/policy/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/kubernetes"
)

// Reader is a read-only cluster view. It only issues get/list requests (and log reads).
type Reader struct {
	cs  kubernetes.Interface
	dyn dynamic.Interface // optional: only needed to read custom resources (e.g. Karpenter)
}

// New wraps a clientset (a fake one in tests).
func New(cs kubernetes.Interface) *Reader { return &Reader{cs: cs} }

// ListPods implements engine.ClusterReader.
func (r *Reader) ListPods(ctx context.Context, namespace string) ([]corev1.Pod, error) {
	list, err := r.cs.CoreV1().Pods(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("list pods: %w", err)
	}
	return list.Items, nil
}

// WithDynamic enables reading custom resources. It returns the reader so it can be chained after New.
func (r *Reader) WithDynamic(d dynamic.Interface) *Reader {
	r.dyn = d
	return r
}

// ListCustomResources implements engine.ClusterReader. A missing CRD (404 / no REST mapping) means "not installed".
func (r *Reader) ListCustomResources(ctx context.Context, gvr schema.GroupVersionResource) ([]unstructured.Unstructured, bool, error) {
	if r.dyn == nil {
		return nil, false, nil
	}
	list, err := r.dyn.Resource(gvr).List(ctx, metav1.ListOptions{})
	if err != nil {
		if apierrors.IsNotFound(err) || meta.IsNoMatchError(err) {
			return nil, false, nil
		}
		return nil, false, fmt.Errorf("list %s: %w", gvr.Resource, err)
	}
	return list.Items, true, nil
}

// ListPDBs implements engine.ClusterReader.
func (r *Reader) ListPDBs(ctx context.Context, namespace string) ([]policyv1.PodDisruptionBudget, error) {
	list, err := r.cs.PolicyV1().PodDisruptionBudgets(namespace).List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("list poddisruptionbudgets: %w", err)
	}
	return list.Items, nil
}

// ListNodes implements engine.ClusterReader.
func (r *Reader) ListNodes(ctx context.Context) ([]corev1.Node, error) {
	list, err := r.cs.CoreV1().Nodes().List(ctx, metav1.ListOptions{})
	if err != nil {
		return nil, fmt.Errorf("list nodes: %w", err)
	}
	return list.Items, nil
}

// PodLogs implements engine.ClusterReader.
func (r *Reader) PodLogs(ctx context.Context, namespace, pod, container string, previous bool, tailLines int64) (string, error) {
	opts := &corev1.PodLogOptions{Container: container, Previous: previous, TailLines: &tailLines}
	stream, err := r.cs.CoreV1().Pods(namespace).GetLogs(pod, opts).Stream(ctx)
	if err != nil {
		return "", fmt.Errorf("get logs for %s/%s: %w", namespace, pod, err)
	}
	defer func() { _ = stream.Close() }() // read-only stream: a close error carries no information we can act on
	data, err := io.ReadAll(io.LimitReader(stream, 64*1024))
	if err != nil {
		return "", fmt.Errorf("read logs for %s/%s: %w", namespace, pod, err)
	}
	return string(data), nil
}

// PodEvents implements engine.ClusterReader. Filtering is repeated client-side because not every
// API server (or fake) honours field selectors.
func (r *Reader) PodEvents(ctx context.Context, namespace, pod string) ([]corev1.Event, error) {
	list, err := r.cs.CoreV1().Events(namespace).List(ctx, metav1.ListOptions{
		FieldSelector: "involvedObject.kind=Pod,involvedObject.name=" + pod,
	})
	if err != nil {
		return nil, fmt.Errorf("list events for %s/%s: %w", namespace, pod, err)
	}
	var out []corev1.Event
	for _, e := range list.Items {
		if e.InvolvedObject.Kind == "Pod" && e.InvolvedObject.Name == pod {
			out = append(out, e)
		}
	}
	return out, nil
}

// GetPVC implements engine.ClusterReader.
func (r *Reader) GetPVC(ctx context.Context, namespace, name string) (*corev1.PersistentVolumeClaim, error) {
	pvc, err := r.cs.CoreV1().PersistentVolumeClaims(namespace).Get(ctx, name, metav1.GetOptions{})
	if err != nil {
		return nil, fmt.Errorf("get pvc %s/%s: %w", namespace, name, err)
	}
	return pvc, nil
}

type podMetrics struct {
	Containers []struct {
		Name  string `json:"name"`
		Usage struct {
			Memory string `json:"memory"`
		} `json:"usage"`
	} `json:"containers"`
}

// PodMemoryUsage implements engine.ClusterReader using the metrics.k8s.io API. When metrics-server
// is not installed (or the clientset has no REST client, as with the fake) it returns ok=false.
func (r *Reader) PodMemoryUsage(ctx context.Context, namespace, pod string) (map[string]int64, bool, error) {
	rc := r.cs.CoreV1().RESTClient()
	if rc == nil || reflect.ValueOf(rc).IsNil() {
		return nil, false, nil
	}
	raw, err := rc.Get().AbsPath("/apis/metrics.k8s.io/v1beta1/namespaces/" + namespace + "/pods/" + pod).DoRaw(ctx)
	if err != nil {
		// 404 / service unavailable: metrics are simply not available, not a failure of the rule.
		return nil, false, nil
	}
	var pm podMetrics
	if err := json.Unmarshal(raw, &pm); err != nil {
		return nil, false, fmt.Errorf("decode pod metrics: %w", err)
	}
	usage := make(map[string]int64, len(pm.Containers))
	for _, c := range pm.Containers {
		q, err := resource.ParseQuantity(c.Usage.Memory)
		if err != nil {
			continue
		}
		usage[c.Name] = q.Value()
	}
	return usage, true, nil
}
