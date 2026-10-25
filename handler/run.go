package handler

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"net"
	"net/http"
	"os"
	"os/signal"
	"runtime"
	"strconv"
	"strings"
	"syscall"
	"time"
)

const exitUsage = 2

var errUsage = errors.New("usage")
var errBackgroundUnavailable = errors.New("background startup unavailable")

func commandConfig(args []string) (Config, []string, bool, error) {
	fs := flag.NewFlagSet("nib-handler", flag.ContinueOnError)
	fs.SetOutput(os.Stderr)
	configPath := fs.String("config", "", "path to config.yaml")
	host := fs.String("host", "", "bind address (overrides config; loopback only)")
	port := fs.String("port", "", "listen port (overrides config)")
	force := fs.Bool("force", false, "stop a Handler that cannot be authenticated through its session")
	if err := fs.Parse(args); err != nil {
		return Config{}, nil, false, errUsage
	}
	cfg, err := Load(*configPath)
	if err != nil {
		return Config{}, nil, false, err
	}
	cfg, err = ApplyOverrides(cfg, *host, *port)
	if err != nil {
		return Config{}, nil, false, err
	}
	if err := RequireLoopbackHost(cfg.Handler.Host); err != nil {
		return Config{}, nil, false, err
	}
	if err := RequireLoopbackHost(cfg.Review.Host); err != nil {
		return Config{}, nil, false, fmt.Errorf("review host: %w", err)
	}
	if err := RequireLoopbackHost(cfg.Diagnostics.Host); err != nil {
		return Config{}, nil, false, fmt.Errorf("diagnostics host: %w", err)
	}
	if cfg.Handler.Port < 1 || cfg.Handler.Port > 65535 || cfg.Review.Port < 1 || cfg.Review.Port > 65535 || cfg.Diagnostics.Port < 1 || cfg.Diagnostics.Port > 65535 {
		return Config{}, nil, false, fmt.Errorf("handler, review and diagnostics ports must be in 1..65535")
	}
	return cfg, fs.Args(), *force, nil
}

// Prepare loads config, applies flags, and checks loopback binds without listening.
func Prepare(args []string) (*Server, string, error) {
	cfg, remaining, _, err := commandConfig(args)
	if err != nil {
		return nil, "", err
	}
	if len(remaining) != 0 {
		return nil, "", fmt.Errorf("unexpected arguments: %v", remaining)
	}
	return NewServer(cfg), net.JoinHostPort(cfg.Handler.Host, strconv.Itoa(cfg.Handler.Port)), nil
}

func sourceName(cfg Config) string {
	if cfg.Source == "" {
		return "defaults"
	}
	return cfg.Source
}

func prepareStartAddress(cfg Config) (bool, error) {
	identity, err := probeAddress(context.Background(), addressURL(cfg.Handler))
	if errors.Is(err, errNotRunning) {
		return true, nil
	}
	if err != nil {
		return false, err
	}
	if identity.ConfigID == configID(cfg) && identity.State == "running" {
		return false, nil
	}
	return false, fmt.Errorf("Handler %s is already running at %s without a verifiable control record; restore its session before upgrading", identity.InstanceID, identity.URL)
}

func prepareStart(cfg Config) (bool, error) { return ensureSingleton(cfg) }

// Main is the process entry used by cmd/nib-handler. macOS launchers invoke start;
// serve remains the explicit foreground entry and the Linux/Windows default.
func Main(args []string) int {
	force := false
	if len(args) > 0 && args[0] == "diagnostics" {
		return DiagnosticsCLI(args[1:])
	}
	command, uri := "serve", ""
	if len(args) > 0 {
		switch args[0] {
		case "serve", "serve-child", "start", "status", "stop", "restart", "connect", "uninstall":
			command, args = args[0], args[1:]
		default:
			if strings.HasPrefix(args[0], "nib://") {
				command, uri, args = "connect", args[0], args[1:]
			}
		}
	}
	var err error
	var cfg Config
	var remaining []string
	cfg, remaining, force, err = commandConfig(args)
	if err != nil {
		if !errors.Is(err, errUsage) {
			fmt.Fprintf(os.Stderr, "error: %v\n", err)
		}
		return exitUsage
	}
	if len(remaining) == 1 && strings.HasPrefix(remaining[0], "nib://") && uri == "" && (command == "serve" || command == "connect") {
		command, uri, remaining = "connect", remaining[0], nil
	}
	if len(remaining) != 0 || (command == "connect" && uri == "") {
		fmt.Fprintln(os.Stderr, "use: nib-handler [start|serve|status|stop|restart|connect] -config config.yaml [-force] [nib://connect?origin=...]")
		return exitUsage
	}
	release := func() {}
	if command != "serve-child" {
		unlock, lockErr := singletonLock("lifecycle")
		if lockErr != nil {
			fmt.Fprintln(os.Stderr, lockErr)
			return 1
		}
		locked := true
		release = func() {
			if locked {
				locked = false
				unlock()
			}
		}
		defer release()
	}
	if command == "uninstall" {
		if uninstallErr := uninstallInstance(cfg); uninstallErr != nil {
			fmt.Fprintln(os.Stderr, uninstallErr)
			return 1
		}
		fmt.Fprintln(os.Stdout, "Handler stopped and local service registration removed; user data preserved")
		return 0
	}
	if command == "serve" || command == "serve-child" {
		return serve(cfg, release)
	}
	if command == "connect" {
		origin, parseErr := ParseConnectURI(uri)
		if parseErr != nil {
			fmt.Fprintf(os.Stderr, "error: %v\n", parseErr)
			return exitUsage
		}
		if !originAllowed(cfg, origin) {
			fmt.Fprintf(os.Stderr, "error: Hub %s is not listed in runtime.allowed_origins in %s; use the configured Hub or update the deployment configuration\n", origin, sourceName(cfg))
			return 1
		}
	}
	if command == "stop" || command == "restart" {
		if err = stopInstance(cfg, force); err == nil && command == "stop" {
			fmt.Fprintf(os.Stdout, "Handler stopped (config %s)\n", sourceName(cfg))
			return 0
		}
	}
	if err == nil && (command == "start" || command == "restart" || command == "connect") {
		var needsStart bool
		needsStart, err = prepareStart(cfg)
		if err == nil && needsStart {
			if runtime.GOOS != "darwin" {
				return serve(cfg, release)
			}
			err = startBackground(cfg)
		}
	}
	if err == nil {
		var identity identityResponse
		if session, sessionErr := discoverSession(cfg); sessionErr == nil {
			identity, err = probeAddress(context.Background(), session.URL)
		} else {
			identity, err = probeInstance(context.Background(), cfg)
		}
		if err == nil {
			fmt.Fprintf(os.Stdout, "Handler %s: %s; results %s; config %s\n", identity.State, identity.URL, identity.ReviewURL, sourceName(cfg))
			if directory, dirErr := sessionDirectory(cfg); dirErr == nil {
				fmt.Fprintf(os.Stdout, "Local service files: %s\n", directory)
			}
			return 0
		}
	}
	if errors.Is(err, errBackgroundUnavailable) {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		return 5
	}
	fmt.Fprintf(os.Stderr, "error: %v\n", err)
	return 1
}

func serve(cfg Config, release func()) int {
	needsStart, err := prepareStart(cfg)
	if err == nil && !needsStart {
		fmt.Fprintf(os.Stdout, "Using running Handler at %s (config %s)\n", addressURL(cfg.Handler), sourceName(cfg))
		return 0
	}
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		return 1
	}
	unlock, lockErr := singletonLock("running")
	if lockErr != nil {
		fmt.Fprintln(os.Stderr, lockErr)
		return 1
	}
	defer unlock()
	address := net.JoinHostPort(cfg.Handler.Host, strconv.Itoa(cfg.Handler.Port))
	reviewAddress := net.JoinHostPort(cfg.Review.Host, strconv.Itoa(cfg.Review.Port))
	apiListener, err := net.Listen("tcp", address)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: handler listener %s: %v\n", address, err)
		return 1
	}
	defer apiListener.Close()
	reviewListener, err := net.Listen("tcp", reviewAddress)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: review listener %s: %v\n", reviewAddress, err)
		return 1
	}
	defer reviewListener.Close()

	srv := NewServer(cfg)
	defer srv.stopDiagnosticsViewer()
	digest, err := executableDigest()
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	if err := saveSession(cfg, localSession{ConfigID: configID(cfg), InstanceID: srv.instanceID, URL: addressURL(cfg.Handler), ReviewURL: addressURL(cfg.Review), PID: os.Getpid(), Token: srv.Token, BinarySHA256: digest, ConfigSource: cfg.Source}); err != nil {
		fmt.Fprintf(os.Stderr, "error: cannot create private local control session: %v\n", err)
		return 1
	}
	defer removeSession(cfg, srv.instanceID)
	if err := registerInstall(cfg); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	release()
	apiServer := &http.Server{Handler: srv.Routes(), ReadHeaderTimeout: 10 * time.Second, IdleTimeout: time.Minute}
	reviewServer := &http.Server{Handler: srv.ReviewRoutes(), ReadHeaderTimeout: 10 * time.Second, IdleTimeout: time.Minute}
	defer apiServer.Close()
	defer reviewServer.Close()
	errs := make(chan error, 2)
	go func() { errs <- apiServer.Serve(apiListener) }()
	go func() { errs <- reviewServer.Serve(reviewListener) }()
	srv.note("handler ready at " + addressURL(cfg.Handler))
	go srv.RefreshResources()
	go srv.ensureDiagnosticsImage()
	fmt.Fprintf(os.Stderr, "nib-handler %s ready on %s (config %s); results %s\n", Version, addressURL(cfg.Handler), sourceName(cfg), addressURL(cfg.Review))

	signals := make(chan os.Signal, 1)
	signal.Notify(signals, os.Interrupt, syscall.SIGTERM, syscall.SIGHUP)
	defer signal.Stop(signals)
	for {
		select {
		case sig := <-signals:
			if err := srv.requestShutdown(); err != nil {
				fmt.Fprintf(os.Stderr, "refusing shutdown on %s: %v\n", sig, err)
			}
		case <-srv.shutdown:
			ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
			_ = apiServer.Shutdown(ctx)
			_ = reviewServer.Shutdown(ctx)
			cancel()
			srv.note("handler stopped")
			return 0
		case err := <-errs:
			if err != nil && !errors.Is(err, http.ErrServerClosed) {
				fmt.Fprintf(os.Stderr, "handler stopped: %v\n", err)
				return 1
			}
		}
	}
}
