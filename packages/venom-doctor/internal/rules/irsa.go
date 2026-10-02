package rules

import (
	"context"
	"fmt"
	"regexp"
	"sort"
	"strings"

	corev1 "k8s.io/api/core/v1"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/engine"
	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/redact"
)

// IRSAConsistency checks that IAM roles for service accounts (IRSA) are wired consistently inside the cluster: the
// ServiceAccount annotation is a valid role ARN and the Pods that use it carry the matching credentials the EKS Pod
// Identity Webhook injects. It does not (and cannot, from Kubernetes alone) check IAM trust policies, permissions or
// EKS Pod Identity associations: see ADR 0008.
type IRSAConsistency struct{ Namespace string }

// ID implements engine.Rule.
func (IRSAConsistency) ID() string { return "KD-K8S-009" }

// Description implements engine.Rule.
func (IRSAConsistency) Description() string {
	return "IRSA wiring: malformed role ARN annotations and Pods that were not injected or carry a stale role"
}

const (
	irsaAnnotation = "eks.amazonaws.com/role-arn"
	envRoleARN     = "AWS_ROLE_ARN"
	envTokenFile   = "AWS_WEB_IDENTITY_TOKEN_FILE"
	maxPodsListed  = 5
)

// roleARNRe is the shape of an IAM role ARN in any AWS partition (aws, aws-cn, aws-us-gov, aws-iso...).
var roleARNRe = regexp.MustCompile(`^arn:aws[a-z-]*:iam::(\d{12}):role/(.+)$`)

func splitRoleARN(arn string) (account, role string, ok bool) {
	m := roleARNRe.FindStringSubmatch(arn)
	if m == nil {
		return "", "", false
	}
	return m[1], m[2], true
}

// podRoleEnv reports the AWS_ROLE_ARN a Pod carries (literal values only) and whether any IRSA variable is present.
func podRoleEnv(pod *corev1.Pod) (roleARN string, haveRole, haveAny bool) {
	scan := func(cs []corev1.Container) {
		for _, c := range cs {
			for _, e := range c.Env {
				switch e.Name {
				case envRoleARN:
					haveAny = true
					if e.ValueFrom == nil {
						roleARN, haveRole = e.Value, true
					}
				case envTokenFile:
					haveAny = true
				}
			}
		}
	}
	scan(pod.Spec.InitContainers)
	scan(pod.Spec.Containers)
	return roleARN, haveRole, haveAny
}

func serviceAccountOf(pod *corev1.Pod) string {
	if pod.Spec.ServiceAccountName != "" {
		return pod.Spec.ServiceAccountName
	}
	return "default"
}

// listPods summarises affected pods without flooding the finding: the first few names and how many are left.
func listPods(names []string) string {
	sort.Strings(names)
	if len(names) <= maxPodsListed {
		return strings.Join(names, ", ")
	}
	return fmt.Sprintf("%s y %d más", strings.Join(names[:maxPodsListed], ", "), len(names)-maxPodsListed)
}

// describeARNDiff says what differs between two role ARNs whose account IDs are masked in the output.
func describeARNDiff(want, got string) string {
	wa, wr, ok1 := splitRoleARN(want)
	ga, gr, ok2 := splitRoleARN(got)
	switch {
	case !ok1 || !ok2:
		return "el formato del ARN"
	case wa != ga && wr != gr:
		return "la cuenta y el nombre del rol"
	case wa != ga:
		return "la cuenta de AWS (los IDs de cuenta se enmascaran en este informe)"
	default:
		return "el nombre del rol"
	}
}

// Check implements engine.Rule.
func (r IRSAConsistency) Check(ctx context.Context, cluster engine.ClusterReader) ([]findings.Finding, error) {
	sas, err := cluster.ListServiceAccounts(ctx, r.Namespace)
	if err != nil {
		return nil, err
	}
	annotated := map[string]*corev1.ServiceAccount{} // "ns/name" -> SA
	for i := range sas {
		if sas[i].Annotations[irsaAnnotation] != "" {
			annotated[sas[i].Namespace+"/"+sas[i].Name] = &sas[i]
		}
	}
	if len(annotated) == 0 {
		return nil, nil
	}
	pods, err := cluster.ListPods(ctx, r.Namespace)
	if err != nil {
		return nil, err
	}

	type group struct{ notInjected, stale []string }
	byPods := map[string]*group{}
	staleGot := map[string]string{} // SA -> one example of the Pod's ARN, for the evidence
	for i := range pods {
		pod := &pods[i]
		key := pod.Namespace + "/" + serviceAccountOf(pod)
		sa := annotated[key]
		if sa == nil || pod.DeletionTimestamp != nil || pod.Status.Phase == corev1.PodSucceeded || pod.Status.Phase == corev1.PodFailed {
			continue
		}
		want := sa.Annotations[irsaAnnotation]
		if _, _, ok := splitRoleARN(want); !ok {
			continue // reported as a malformed annotation; comparing Pods against it would only add noise
		}
		got, haveRole, haveAny := podRoleEnv(pod)
		g := byPods[key]
		if g == nil {
			g = &group{}
			byPods[key] = g
		}
		switch {
		case !haveAny:
			g.notInjected = append(g.notInjected, pod.Name)
		case haveRole && got != want:
			g.stale = append(g.stale, pod.Name)
			if _, seen := staleGot[key]; !seen {
				staleGot[key] = got
			}
		}
	}

	keys := make([]string, 0, len(annotated))
	for k := range annotated {
		keys = append(keys, k)
	}
	sort.Strings(keys)

	var out []findings.Finding
	for _, key := range keys {
		sa := annotated[key]
		res := findings.Resource{Type: "ServiceAccount", Name: sa.Name, Namespace: sa.Namespace}
		want := sa.Annotations[irsaAnnotation]
		annotationEv := findings.Evidence{Kind: "serviceaccount-annotation", Detail: redact.String(irsaAnnotation + "=" + truncate(want, 200))}

		if _, _, ok := splitRoleARN(want); !ok {
			out = append(out, newFindingFor(r.ID(), findings.SeverityHigh,
				"El ServiceAccount "+sa.Name+" tiene un ARN de rol mal formado", res,
				[]findings.Evidence{annotationEv},
				"La anotación no tiene la forma de un ARN de rol de IAM (arn:aws:iam::<cuenta de 12 dígitos>:role/<nombre>); los Pods recibirían un rol inválido y las llamadas a AWS fallarían.",
				findings.SuggestedFix{Summary: "Corregir el ARN de la anotación del ServiceAccount.", Steps: []string{
					"Copiar el ARN exacto del rol desde IAM (consola o aws iam get-role --role-name <nombre>) y ponerlo en la anotación " + irsaAnnotation + ".",
					"Recrear los Pods del workload después de corregirla: el webhook solo inyecta el valor al crearlos."}},
				findings.RiskLow,
				[]string{"https://docs.aws.amazon.com/eks/latest/userguide/associate-service-account-role.html"},
				[]string{"irsa", "iam", "irsa-malformed-arn"}))
			continue
		}
		g := byPods[key]
		if g == nil {
			continue
		}
		if n := len(g.notInjected); n > 0 {
			out = append(out, newFindingFor(r.ID(), findings.SeverityHigh,
				fmt.Sprintf("Pods del ServiceAccount %s sin credenciales de IRSA", sa.Name), res,
				[]findings.Evidence{annotationEv,
					{Kind: "pods-not-injected", Detail: fmt.Sprintf("%s sin %s ni %s: %s", podCount(n), envRoleARN, envTokenFile, listPods(g.notInjected))}},
				"El ServiceAccount está anotado con un rol, pero estos Pods no recibieron las variables de IRSA: se crearon antes de añadir la anotación o el webhook no estaba disponible al crearlos. Sin ellas, el SDK no usa el rol y puede caer a las credenciales del nodo.",
				findings.SuggestedFix{Summary: "Recrear los Pods para que el webhook inyecte las credenciales de IRSA.", Steps: []string{
					"Comprobar que el Amazon EKS Pod Identity Webhook está sano en el clúster (en EKS es un componente gestionado).",
					"Recrear los Pods del workload (por ejemplo kubectl rollout restart del Deployment o StatefulSet) y comprobar que ahora aparece " + envRoleARN + " con kubectl describe pod.",
					"Si siguen sin inyectarse, revisar el webhook mutante y los eventos del Pod."}},
				findings.RiskMedium,
				[]string{"https://docs.aws.amazon.com/eks/latest/userguide/pod-configuration.html"},
				[]string{"irsa", "iam", "irsa-pods-not-injected"}))
		}
		if n := len(g.stale); n > 0 {
			diff := describeARNDiff(want, staleGot[key])
			out = append(out, newFindingFor(r.ID(), findings.SeverityMedium,
				fmt.Sprintf("Pods del ServiceAccount %s con un rol de IRSA desactualizado", sa.Name), res,
				[]findings.Evidence{annotationEv,
					{Kind: "pod-role-arn", Detail: redact.String(fmt.Sprintf("%s del Pod = %s; difiere %s", envRoleARN, truncate(staleGot[key], 200), diff))},
					{Kind: "pods-stale", Detail: fmt.Sprintf("%s afectado(s): %s", podCount(n), listPods(g.stale))}},
				"La anotación del ServiceAccount cambió después de crear estos Pods: siguen usando el rol anterior, porque el webhook solo inyecta el valor al crear el Pod.",
				findings.SuggestedFix{Summary: "Recrear los Pods para que tomen el rol actual de la anotación.", Steps: []string{
					"Confirmar que el ARN de la anotación es el que se quiere (en IAM) y no un cambio accidental.",
					"Recrear los Pods del workload (por ejemplo kubectl rollout restart) para que reciban el ARN actual."}},
				findings.RiskMedium,
				[]string{"https://docs.aws.amazon.com/eks/latest/userguide/pod-configuration.html"},
				[]string{"irsa", "iam", "irsa-role-arn-stale"}))
		}
	}
	return out, nil
}
