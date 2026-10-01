package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"time"

	"github.com/spf13/cobra"
	"k8s.io/cli-runtime/pkg/genericclioptions"
	"k8s.io/client-go/dynamic"
	"k8s.io/client-go/kubernetes"

	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/cluster"
	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/engine"
	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/explain"
	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/output"
	"github.com/CloudVipers/VenomOps/packages/kdoctor/internal/rules"
)

// modelEnv names the environment variable holding the default Bedrock model for --explain.
const modelEnv = "KDOCTOR_BEDROCK_MODEL"

type options struct {
	namespace     string // resolved namespace ("" = all)
	output        string
	explain       bool
	explainModel  string
	allNamespaces bool
}

func newRootCmd(stdout, stderr io.Writer) *cobra.Command {
	configFlags := genericclioptions.NewConfigFlags(true)
	var o options

	cmd := &cobra.Command{
		Use:   "kubectl-venom_doctor",
		Short: "Explica por qué algo está roto en tu clúster y cómo arreglarlo (solo lectura)",
		Long: `kubectl venom-doctor revisa los Pods de un namespace (o de todos) y explica en lenguaje claro
por qué están fallando y cómo arreglarlo: CrashLoopBackOff, OOMKilled, ImagePullBackOff, Pending,
probes fallando, PDB que bloquean drains y ServiceAccounts de IRSA mal cableados. Con -A revisa
además los nodos NotReady y, si está instalado, Karpenter (NodePools y NodeClaims).

Es de solo lectura: nunca modifica el clúster. La explicación con IA (--explain) es opcional,
está desactivada por defecto y solo envía el hallazgo con los secretos ya enmascarados.`,
		Example: `  kubectl venom-doctor                       # namespace actual
  kubectl venom-doctor -n payments           # un namespace
  kubectl venom-doctor -A -o json            # todos los namespaces, salida JSON (findings-schema)
  kubectl venom-doctor --explain --explain-model <modelId>`,
		Version:       version,
		SilenceUsage:  true,
		SilenceErrors: true,
		Args:          cobra.NoArgs,
		RunE: func(cmd *cobra.Command, _ []string) error {
			if _, err := output.ParseFormat(o.output); err != nil {
				return err
			}
			restConfig, err := configFlags.ToRESTConfig()
			if err != nil {
				return fmt.Errorf("load kubeconfig: %w", err)
			}
			restConfig.UserAgent = "kubectl-venom_doctor/" + version
			cs, err := kubernetes.NewForConfig(restConfig)
			if err != nil {
				return fmt.Errorf("create kubernetes client: %w", err)
			}
			dyn, err := dynamic.NewForConfig(restConfig)
			if err != nil {
				return fmt.Errorf("create dynamic client: %w", err)
			}

			ns := ""
			if !o.allNamespaces {
				ns, _, err = configFlags.ToRawKubeConfigLoader().Namespace()
				if err != nil {
					return fmt.Errorf("resolve namespace: %w", err)
				}
			}
			o.namespace = ns

			var explainer explain.Explainer
			if o.explain {
				explainer = buildExplainer(cmd.Context(), o, stderr)
			}
			return run(cmd.Context(), o, cluster.New(cs).WithDynamic(dyn), explainer, stdout, stderr)
		},
	}

	configFlags.AddFlags(cmd.Flags()) // --kubeconfig, --context, -n/--namespace, ... (kubectl-compatible)
	cmd.Flags().BoolVarP(&o.allNamespaces, "all-namespaces", "A", false, "Revisar todos los namespaces")
	cmd.Flags().StringVarP(&o.output, "output", "o", "table", "Formato de salida: table|json")
	cmd.Flags().BoolVar(&o.explain, "explain", false, "Agregar una explicación ampliada con Amazon Bedrock (opcional; envía el hallazgo sin secretos)")
	cmd.Flags().StringVar(&o.explainModel, "explain-model", "", "Modelo de Bedrock para --explain (o variable "+modelEnv+")")
	return cmd
}

// warnf writes a warning to stderr; a failing stderr is not actionable, so the error is dropped on purpose.
func warnf(w io.Writer, format string, a ...any) { _, _ = fmt.Fprintf(w, format, a...) }

// buildExplainer returns nil (with a warning) whenever the AI step cannot run, so the diagnosis
// always completes.
func buildExplainer(ctx context.Context, o options, stderr io.Writer) explain.Explainer {
	model := o.explainModel
	if model == "" {
		model = os.Getenv(modelEnv)
	}
	b, err := explain.NewBedrock(ctx, model)
	if err != nil {
		if errors.Is(err, explain.ErrNoCredentials) {
			warnf(stderr, "aviso: --explain requiere credenciales de AWS; se omite la explicación con IA.\n")
		} else {
			warnf(stderr, "aviso: no se pudo activar --explain: %v\n", err)
		}
		return nil
	}
	return b
}

// run executes the rules, optionally adds AI explanations, and renders the result.
func run(ctx context.Context, o options, reader engine.ClusterReader, explainer explain.Explainer, stdout, stderr io.Writer) error {
	format, err := output.ParseFormat(o.output)
	if err != nil {
		return err
	}
	eng, err := engine.New(rules.Default(o.namespace)...)
	if err != nil {
		return err
	}

	runCtx, cancel := context.WithTimeout(ctx, 2*time.Minute)
	defer cancel()
	report := eng.Run(runCtx, reader)

	if explainer != nil {
		for _, w := range explain.Apply(runCtx, explainer, report.Findings) {
			warnf(stderr, "aviso: %s\n", w)
		}
	}

	switch format {
	case output.FormatJSON:
		for _, re := range report.Errors { // keep stdout pure JSON: rule errors go to stderr
			warnf(stderr, "aviso: %v\n", re)
		}
		return output.JSON(stdout, report.Findings)
	default:
		return output.Table(stdout, report.Findings, report.Errors)
	}
}
