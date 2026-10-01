package rules

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"

	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/engine"
)

const (
	roleA = "arn:aws:iam::123456789012:role/app-a"
	roleB = "arn:aws:iam::123456789012:role/app-b"
)

func irsaSA(ns, name, arn string) *corev1.ServiceAccount {
	sa := &corev1.ServiceAccount{ObjectMeta: metav1.ObjectMeta{Namespace: ns, Name: name}}
	if arn != "" {
		sa.Annotations = map[string]string{irsaAnnotation: arn}
	}
	return sa
}

// irsaPod returns a Pod of the given ServiceAccount; env sets its container variables.
func irsaPod(ns, name, sa string, env ...corev1.EnvVar) *corev1.Pod {
	return pod(ns, name, func(p *corev1.Pod) {
		p.Spec.ServiceAccountName = sa
		p.Spec.Containers[0].Env = env
	})
}

func injected(arn string) []corev1.EnvVar {
	return []corev1.EnvVar{
		{Name: envRoleARN, Value: arn},
		{Name: envTokenFile, Value: "/var/run/secrets/eks.amazonaws.com/serviceaccount/token"},
	}
}

func TestIRSAMalformedARN(t *testing.T) {
	for _, bad := range []string{"my-role", "arn:aws:iam::123:role/short", "arn:aws:s3:::bucket", "arn:aws:iam::123456789012:user/bob"} {
		t.Run(bad, func(t *testing.T) {
			fs := check(t, IRSAConsistency{}, newCluster(irsaSA("web", "app", bad), irsaPod("web", "p1", "app")))
			if len(fs) != 1 || fs[0].Resource.Type != "ServiceAccount" || fs[0].Severity != "high" || !hasTag(fs[0].Tags, "irsa-malformed-arn") {
				t.Fatalf("malformed %q: %+v", bad, fs)
			}
		})
	}
	// A single affected Pod is not "1 Pods".
	one := check(t, IRSAConsistency{}, newCluster(irsaSA("web", "app", roleA), irsaPod("web", "p1", "app")))
	if len(one) != 1 || !strings.HasPrefix(evidenceKinds(one[0])["pods-not-injected"], "1 Pod sin") {
		t.Fatalf("singular: %+v", one)
	}
	// Other partitions are valid.
	for _, ok := range []string{"arn:aws-cn:iam::123456789012:role/r", "arn:aws-us-gov:iam::123456789012:role/path/r"} {
		if fs := check(t, IRSAConsistency{}, newCluster(irsaSA("web", "app", ok), irsaPod("web", "p1", "app", injected(ok)...))); len(fs) != 0 {
			t.Fatalf("valid ARN %q reported: %+v", ok, fs)
		}
	}
}

func TestIRSAMalformedARNDoesNotAlsoReportItsPods(t *testing.T) {
	fs := check(t, IRSAConsistency{}, newCluster(irsaSA("web", "app", "nope"), irsaPod("web", "p1", "app"), irsaPod("web", "p2", "app", injected(roleA)...)))
	if len(fs) != 1 {
		t.Fatalf("a malformed annotation must be one finding, got %d: %+v", len(fs), fs)
	}
}

func TestIRSAPodsNotInjectedAreGroupedPerServiceAccount(t *testing.T) {
	var pods []*corev1.Pod
	for i := 1; i <= 8; i++ {
		pods = append(pods, irsaPod("web", fmt.Sprintf("p%d", i), "app"))
	}
	pods = append(pods, irsaPod("web", "ok", "app", injected(roleA)...))
	c := newCluster(irsaSA("web", "app", roleA), pods[0], pods[1], pods[2], pods[3], pods[4], pods[5], pods[6], pods[7], pods[8])
	fs := check(t, IRSAConsistency{}, c)
	if len(fs) != 1 {
		t.Fatalf("want 1 grouped finding for 8 pods, got %d", len(fs))
	}
	f := fs[0]
	if f.Severity != "high" || !hasTag(f.Tags, "irsa-pods-not-injected") || f.RiskOfFix != "medium" {
		t.Fatalf("finding: %+v", f)
	}
	ev := evidenceKinds(f)["pods-not-injected"]
	if !strings.HasPrefix(ev, "8 Pods") || !strings.Contains(ev, "y 3 más") || !strings.Contains(ev, "p1, p2, p3, p4, p5") {
		t.Fatalf("pod summary: %q", ev)
	}
}

func TestIRSAStaleRoleExplainsWhatDiffers(t *testing.T) {
	cases := []struct{ name, podARN, want string }{
		{"different role name", roleB, "el nombre del rol"},
		{"different account", "arn:aws:iam::999999999999:role/app-a", "la cuenta de AWS"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			fs := check(t, IRSAConsistency{}, newCluster(irsaSA("web", "app", roleA), irsaPod("web", "p1", "app", injected(c.podARN)...)))
			if len(fs) != 1 || !hasTag(fs[0].Tags, "irsa-role-arn-stale") || fs[0].Severity != "medium" {
				t.Fatalf("stale: %+v", fs)
			}
			ev := evidenceKinds(fs[0])
			if !strings.Contains(ev["pod-role-arn"], c.want) {
				t.Fatalf("diff not explained: %q", ev["pod-role-arn"])
			}
			if strings.Contains(ev["pod-role-arn"], "123456789012") || strings.Contains(ev["pod-role-arn"], "999999999999") ||
				strings.Contains(ev["serviceaccount-annotation"], "123456789012") {
				t.Fatalf("account IDs must be masked: %v", ev)
			}
		})
	}
}

func TestIRSAIgnoresWhatIsNotAProblem(t *testing.T) {
	done := irsaPod("web", "done", "app")
	done.Status.Phase = corev1.PodSucceeded
	deleting := irsaPod("web", "deleting", "app")
	deleting.DeletionTimestamp = &metav1.Time{Time: now()}
	fromSecret := irsaPod("web", "ref", "app", corev1.EnvVar{Name: envRoleARN, ValueFrom: &corev1.EnvVarSource{
		SecretKeyRef: &corev1.SecretKeySelector{LocalObjectReference: corev1.LocalObjectReference{Name: "s"}, Key: "arn"}}})
	cases := map[string][]runtime.Object{
		"healthy pod":                       {irsaSA("web", "app", roleA), irsaPod("web", "p", "app", injected(roleA)...)},
		"unannotated service account":       {irsaSA("web", "plain", ""), irsaPod("web", "p", "plain")},
		"completed and deleting pods":       {irsaSA("web", "app", roleA), done, deleting},
		"pod of another service account":    {irsaSA("web", "app", roleA), irsaPod("web", "p", "other")},
		"same name in another namespace":    {irsaSA("web", "app", roleA), irsaPod("other", "p", "app")},
		"role taken from a secret (opaque)": {irsaSA("web", "app", roleA), fromSecret},
		"token file only (manual setup)":    {irsaSA("web", "app", roleA), irsaPod("web", "p", "app", corev1.EnvVar{Name: envTokenFile, Value: "/t"})},
	}
	for name, objs := range cases {
		t.Run(name, func(t *testing.T) {
			if fs := check(t, IRSAConsistency{}, newCluster(objs...)); len(fs) != 0 {
				t.Fatalf("unexpected findings: %+v", fs)
			}
		})
	}
}

func TestIRSADefaultServiceAccountAndNamespaceScope(t *testing.T) {
	// A Pod without serviceAccountName runs as "default".
	def := pod("web", "p1", nil)
	fs := check(t, IRSAConsistency{}, newCluster(irsaSA("web", "default", roleA), def))
	if len(fs) != 1 || fs[0].Resource.Name != "default" {
		t.Fatalf("the implicit default service account must be matched: %+v", fs)
	}
	a := newCluster(irsaSA("a", "app", roleA), irsaPod("a", "p", "app"), irsaSA("b", "app", roleA), irsaPod("b", "p", "app"))
	if fs := check(t, IRSAConsistency{Namespace: "a"}, a); len(fs) != 1 || fs[0].Resource.Namespace != "a" {
		t.Fatalf("namespace filter: %+v", fs)
	}
	if fs := check(t, IRSAConsistency{}, a); len(fs) != 2 {
		t.Fatalf("all namespaces: %d", len(fs))
	}
}

func TestIRSAOutputIsStable(t *testing.T) {
	c := newCluster(irsaSA("b", "z", roleA), irsaSA("a", "y", roleA), irsaPod("b", "p", "z"), irsaPod("a", "p", "y"))
	first := ""
	for i := 0; i < 15; i++ {
		fs := check(t, IRSAConsistency{}, c)
		got := fs[0].Resource.Namespace + "/" + fs[0].Resource.Name + "," + fs[1].Resource.Namespace + "/" + fs[1].Resource.Name
		if i == 0 {
			first = got
		} else if got != first {
			t.Fatalf("unstable order: %s vs %s", got, first)
		}
	}
	if first != "a/y,b/z" {
		t.Fatalf("findings must be sorted by namespace/name, got %s", first)
	}
}

type failingSAs struct{ engine.ClusterReader }

func (failingSAs) ListServiceAccounts(context.Context, string) ([]corev1.ServiceAccount, error) {
	return nil, errors.New("serviceaccounts is forbidden")
}

func TestIRSAReturnsTheListError(t *testing.T) {
	if _, err := (IRSAConsistency{}).Check(context.Background(), failingSAs{newCluster()}); err == nil {
		t.Fatal("a forbidden list must surface as a rule error")
	}
}
