package handler

import (
	"errors"
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
)

const exitUsage = 2

var errUsage = errors.New("usage")

// Prepare loads config, applies flags, and checks loopback binds.
// It does not listen.
func Prepare(args []string) (*Server, string, error) {
	fs := flag.NewFlagSet("nib-handler", flag.ContinueOnError)
	configPath := fs.String("config", "", "path to config.yaml")
	host := fs.String("host", "", "bind address (overrides config; loopback only)")
	port := fs.String("port", "", "listen port (overrides config)")
	fs.SetOutput(os.Stderr)
	if err := fs.Parse(args); err != nil {
		return nil, "", errUsage
	}

	cfg, err := Load(*configPath)
	if err != nil {
		return nil, "", err
	}
	cfg, err = ApplyOverrides(cfg, *host, *port)
	if err != nil {
		return nil, "", err
	}
	if err := RequireLoopbackHost(cfg.Handler.Host); err != nil {
		return nil, "", err
	}
	if err := RequireLoopbackHost(cfg.Review.Host); err != nil {
		return nil, "", fmt.Errorf("review host: %w", err)
	}

	addr := net.JoinHostPort(cfg.Handler.Host, strconv.Itoa(cfg.Handler.Port))
	return NewServer(cfg), addr, nil
}

// Main is the process entry used by cmd/nib-handler.
func Main(args []string) int {
	srv, addr, err := Prepare(args)
	if err != nil {
		if !errors.Is(err, errUsage) {
			fmt.Fprintf(os.Stderr, "error: %v\n", err)
		}
		return exitUsage
	}
	source := srv.Config.Source
	if source == "" {
		source = "defaults"
	}
	fmt.Fprintf(os.Stderr, "nib-handler %s on http://%s (config %s)\n", Version, addr, source)
	if err := http.ListenAndServe(addr, srv.Routes()); err != nil {
		log.Print(err)
		return 1
	}
	return 0
}
