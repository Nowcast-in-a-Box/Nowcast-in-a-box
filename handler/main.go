// Command nib-handler is the host daemon for Nowcast-in-a-Box.
//
// Users run this binary on Linux, macOS, or Windows. It binds loopback
// HTTP, checks Docker, and will start Model Containers through Compose.
// Evaluation and visualization run on the host from review/, started by
// this process. Bind addresses and paths come from config.yaml.
package main

import (
	"flag"
	"fmt"
	"log"
	"net"
	"net/http"
	"os"
	"strconv"
)

const exitUsage = 2

func main() {
	os.Exit(run(os.Args[1:]))
}

func run(args []string) int {
	fs := flag.NewFlagSet("nib-handler", flag.ContinueOnError)
	configPath := fs.String("config", "", "path to config.yaml")
	host := fs.String("host", "", "bind address (overrides config; loopback only)")
	port := fs.String("port", "", "listen port (overrides config)")
	fs.SetOutput(os.Stderr)
	if err := fs.Parse(args); err != nil {
		return exitUsage
	}

	cfg, err := loadConfig(*configPath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		return exitUsage
	}
	cfg, err = applyOverrides(cfg, *host, *port)
	if err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		return exitUsage
	}
	if err := requireLoopbackHost(cfg.Handler.Host); err != nil {
		fmt.Fprintf(os.Stderr, "error: %v\n", err)
		return exitUsage
	}
	if err := requireLoopbackHost(cfg.Review.Host); err != nil {
		fmt.Fprintf(os.Stderr, "error: review host: %v\n", err)
		return exitUsage
	}

	addr := net.JoinHostPort(cfg.Handler.Host, strconv.Itoa(cfg.Handler.Port))
	srv := &server{cfg: cfg}
	source := cfg.Source
	if source == "" {
		source = "defaults"
	}
	fmt.Fprintf(os.Stderr, "nib-handler %s on http://%s (config %s)\n", version, addr, source)
	if err := http.ListenAndServe(addr, srv.routes()); err != nil {
		log.Print(err)
		return 1
	}
	return 0
}
