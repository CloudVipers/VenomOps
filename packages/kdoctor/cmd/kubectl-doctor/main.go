// Command kubectl-doctor is a kubectl plugin (`kubectl doctor`) that explains in plain language why
// something is broken in a cluster and how to fix it. It is strictly read-only.
package main

import (
	"context"
	"fmt"
	"os"
	"os/signal"
	"syscall"
)

// version is set at build time (-ldflags "-X main.version=...").
var version = "dev"

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	if err := newRootCmd(os.Stdout, os.Stderr).ExecuteContext(ctx); err != nil {
		fmt.Fprintln(os.Stderr, "error:", err)
		os.Exit(1)
	}
}
