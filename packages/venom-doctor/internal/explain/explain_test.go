package explain

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/aws/aws-sdk-go-v2/service/bedrockruntime"
	"github.com/aws/aws-sdk-go-v2/service/bedrockruntime/types"

	findings "github.com/CloudVipers/VenomOps/packages/findings-schema/go"
)

func finding() findings.Finding {
	return findings.Finding{
		ID: "VD-K8S-001", Title: "Pod api en CrashLoopBackOff",
		Resource: findings.Resource{Type: "Pod", Name: "api", Namespace: "payments"},
		Evidence: []findings.Evidence{
			{Kind: "log-tail", Detail: "connecting with password=hunter2 to db"},
			{Kind: "event", Detail: "pull https://user:pa55word@registry.example.com/x failed for 123456789012"},
		},
		RootCause: "Falla al conectar a la base de datos.",
	}
}

func TestRedactedJSONMasksSecretsBeforeAnythingIsSent(t *testing.T) {
	got, err := RedactedJSON(finding())
	if err != nil {
		t.Fatal(err)
	}
	for _, leaked := range []string{"hunter2", "pa55word", "123456789012"} {
		if strings.Contains(got, leaked) {
			t.Fatalf("%q leaked into the payload:\n%s", leaked, got)
		}
	}
	if !strings.Contains(got, "CrashLoopBackOff") {
		t.Fatal("useful context must be kept")
	}
}

type fakeConverse struct {
	in  *bedrockruntime.ConverseInput
	out string
	err error
}

func (f *fakeConverse) Converse(_ context.Context, in *bedrockruntime.ConverseInput, _ ...func(*bedrockruntime.Options)) (*bedrockruntime.ConverseOutput, error) {
	f.in = in
	if f.err != nil {
		return nil, f.err
	}
	return &bedrockruntime.ConverseOutput{Output: &types.ConverseOutputMemberMessage{Value: types.Message{
		Role:    types.ConversationRoleAssistant,
		Content: []types.ContentBlock{&types.ContentBlockMemberText{Value: f.out}},
	}}}, nil
}

func TestBedrockExplainSendsOnlyTheGivenPayload(t *testing.T) {
	fc := &fakeConverse{out: "Explicación breve."}
	b := &Bedrock{client: fc, modelID: "test-model", maxTokens: 256}
	text, err := b.Explain(context.Background(), "PAYLOAD")
	if err != nil || text != "Explicación breve." {
		t.Fatalf("%q %v", text, err)
	}
	if *fc.in.ModelId != "test-model" || len(fc.in.Messages) != 1 || *fc.in.InferenceConfig.MaxTokens != 256 {
		t.Fatalf("unexpected request: %+v", fc.in)
	}
	if msg := fc.in.Messages[0].Content[0].(*types.ContentBlockMemberText).Value; msg != "PAYLOAD" {
		t.Fatalf("user message should be exactly the redacted payload, got %q", msg)
	}
}

func TestApplyAppendsExplanationAndToleratesFailures(t *testing.T) {
	ok := []findings.Finding{finding()}
	if w := Apply(context.Background(), &Bedrock{client: &fakeConverse{out: "  texto  "}, modelID: "m", maxTokens: 1}, ok); len(w) != 0 {
		t.Fatalf("warnings: %v", w)
	}
	last := ok[0].Evidence[len(ok[0].Evidence)-1]
	if last.Kind != EvidenceKind || last.Detail != "texto" {
		t.Fatalf("explanation not attached: %+v", last)
	}

	failing := []findings.Finding{finding()}
	before := len(failing[0].Evidence)
	w := Apply(context.Background(), &Bedrock{client: &fakeConverse{err: errors.New("throttled")}, modelID: "m", maxTokens: 1}, failing)
	if len(w) != 1 || !strings.Contains(w[0], "throttled") || len(failing[0].Evidence) != before {
		t.Fatalf("a failing model must only warn: %v %+v", w, failing[0].Evidence)
	}
}

func TestNewBedrockRequiresAModel(t *testing.T) {
	if _, err := NewBedrock(context.Background(), " "); err == nil || !strings.Contains(err.Error(), "VENOM_DOCTOR_BEDROCK_MODEL") {
		t.Fatalf("expected a helpful error, got %v", err)
	}
}

func TestNewBedrockWithoutCredentialsReturnsErrNoCredentials(t *testing.T) {
	// Isolate from any real AWS configuration and from the EC2 metadata endpoint.
	t.Setenv("AWS_EC2_METADATA_DISABLED", "true")
	t.Setenv("AWS_CONFIG_FILE", "/nonexistent")
	t.Setenv("AWS_SHARED_CREDENTIALS_FILE", "/nonexistent")
	for _, k := range []string{"AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE", "AWS_WEB_IDENTITY_TOKEN_FILE"} {
		t.Setenv(k, "")
	}
	t.Setenv("AWS_REGION", "us-east-1")
	if _, err := NewBedrock(context.Background(), "some-model"); !errors.Is(err, ErrNoCredentials) {
		t.Fatalf("expected ErrNoCredentials, got %v", err)
	}
}
