// Package explain adds an OPTIONAL plain-language explanation to findings using Amazon Bedrock.
// It is never used unless the user passes --explain, findings are redacted before leaving the
// machine, and any failure degrades to "no explanation" instead of failing the diagnosis.
package explain

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/bedrockruntime"
	"github.com/aws/aws-sdk-go-v2/service/bedrockruntime/types"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
	"github.com/CloudVipers/VenomOps/packages/venom-doctor/internal/redact"
)

// EvidenceKind is the evidence kind used to attach an explanation to a finding.
const EvidenceKind = "ai-explanation"

// ErrNoCredentials means no AWS credentials could be resolved, so the AI step is skipped.
var ErrNoCredentials = errors.New("no AWS credentials available")

// Explainer produces an extended explanation for one (already redacted) finding.
type Explainer interface {
	Explain(ctx context.Context, redactedFinding string) (string, error)
}

// RedactedJSON serialises a finding with every secret/sensitive identifier masked. This is the ONLY
// representation of a finding that may be sent to a model.
func RedactedJSON(f findings.Finding) (string, error) {
	raw, err := json.MarshalIndent(f, "", "  ")
	if err != nil {
		return "", fmt.Errorf("marshal finding: %w", err)
	}
	return redact.String(string(raw)), nil
}

// Apply asks the explainer about each finding and appends the answer as evidence. Findings whose
// explanation fails are left untouched; the returned warnings describe what went wrong.
func Apply(ctx context.Context, e Explainer, fs []findings.Finding) []string {
	var warnings []string
	for i := range fs {
		payload, err := RedactedJSON(fs[i])
		if err != nil {
			warnings = append(warnings, fmt.Sprintf("%s: %v", fs[i].ID, err))
			continue
		}
		text, err := e.Explain(ctx, payload)
		if err != nil {
			warnings = append(warnings, fmt.Sprintf("explain %s %s: %v", fs[i].ID, fs[i].Resource.Name, err))
			continue
		}
		if text = strings.TrimSpace(text); text != "" {
			fs[i].Evidence = append(fs[i].Evidence, findings.Evidence{Kind: EvidenceKind, Detail: text})
		}
	}
	return warnings
}

const systemPrompt = `Eres un ingeniero SRE experto en Kubernetes. Recibes un hallazgo de diagnóstico en JSON ` +
	`(con secretos ya enmascarados como [REDACTED]). Explica en español, en lenguaje claro y en máximo 8 líneas: ` +
	`qué significa el problema, por qué ocurre aquí y cuál es el siguiente paso más probable. ` +
	`Usa solo la información del hallazgo; si falta información, dilo. No pidas ni inventes credenciales.`

// converser is the subset of the Bedrock runtime client we use (mockable in tests).
type converser interface {
	Converse(ctx context.Context, in *bedrockruntime.ConverseInput, opts ...func(*bedrockruntime.Options)) (*bedrockruntime.ConverseOutput, error)
}

// Bedrock implements Explainer with the Bedrock Converse API.
type Bedrock struct {
	client    converser
	modelID   string
	maxTokens int32
}

// NewBedrock builds a client from the default AWS configuration chain. It returns ErrNoCredentials
// when none are available so the caller can warn and continue without AI.
func NewBedrock(ctx context.Context, modelID string) (*Bedrock, error) {
	if strings.TrimSpace(modelID) == "" {
		return nil, errors.New("no Bedrock model configured (use --explain-model or VENOM_DOCTOR_BEDROCK_MODEL)")
	}
	cfg, err := config.LoadDefaultConfig(ctx)
	if err != nil {
		return nil, fmt.Errorf("load AWS config: %w", err)
	}
	if _, err := cfg.Credentials.Retrieve(ctx); err != nil {
		return nil, fmt.Errorf("%w: %v", ErrNoCredentials, err)
	}
	return &Bedrock{client: bedrockruntime.NewFromConfig(cfg), modelID: modelID, maxTokens: 512}, nil
}

// Explain implements Explainer.
func (b *Bedrock) Explain(ctx context.Context, redactedFinding string) (string, error) {
	out, err := b.client.Converse(ctx, &bedrockruntime.ConverseInput{
		ModelId: aws.String(b.modelID),
		System:  []types.SystemContentBlock{&types.SystemContentBlockMemberText{Value: systemPrompt}},
		Messages: []types.Message{{
			Role:    types.ConversationRoleUser,
			Content: []types.ContentBlock{&types.ContentBlockMemberText{Value: redactedFinding}},
		}},
		InferenceConfig: &types.InferenceConfiguration{MaxTokens: aws.Int32(b.maxTokens), Temperature: aws.Float32(0.2)},
	})
	if err != nil {
		return "", fmt.Errorf("bedrock converse: %w", err)
	}
	msg, ok := out.Output.(*types.ConverseOutputMemberMessage)
	if !ok {
		return "", errors.New("bedrock returned no message")
	}
	var parts []string
	for _, block := range msg.Value.Content {
		if t, ok := block.(*types.ContentBlockMemberText); ok {
			parts = append(parts, t.Value)
		}
	}
	return strings.Join(parts, "\n"), nil
}
