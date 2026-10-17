//go:build !darwin

package handler_test

import (
	"encoding/json"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
	"time"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
	"gopkg.in/yaml.v3"
)

func TestForegroundCLIChild(t *testing.T) {
	if os.Getenv("NIB_TEST_CLI_CHILD") != "1" {
		return
	}
	for i, argument := range os.Args {
		if argument == "--" {
			os.Exit(handler.Main(os.Args[i+1:]))
		}
	}
	os.Exit(2)
}

func TestNonMacLifecycleCommandsCanStartForegroundHandler(t *testing.T) {
	t.Setenv("XDG_CACHE_HOME", t.TempDir())
	t.Setenv("LOCALAPPDATA", t.TempDir())
	for _, command := range []string{"start", "connect", "restart"} {
		t.Run(command, func(t *testing.T) {
			api, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				t.Fatal(err)
			}
			review, err := net.Listen("tcp", "127.0.0.1:0")
			if err != nil {
				api.Close()
				t.Fatal(err)
			}
			cfg := handler.DefaultConfig()
			cfg.Handler.Port = api.Addr().(*net.TCPAddr).Port
			cfg.Review.Port = review.Addr().(*net.TCPAddr).Port
			cfg.Paths.Runs, cfg.Paths.Artifacts = t.TempDir(), t.TempDir()
			cfg.Runtime.AllowedOrigins = []string{"http://hub.example:8000"}
			cfg.Source = filepath.Join(t.TempDir(), "config.yaml")
			raw, _ := yaml.Marshal(cfg)
			if err := os.WriteFile(cfg.Source, raw, 0o600); err != nil {
				t.Fatal(err)
			}
			api.Close()
			review.Close()
			args := []string{"-test.run=^TestForegroundCLIChild$", "--", command, "-config", cfg.Source}
			if command == "connect" {
				args = append(args, "nib://connect?origin=http://hub.example:8000")
			}
			process := exec.Command(os.Args[0], args...)
			process.Env = append(os.Environ(), "NIB_TEST_CLI_CHILD=1")
			if err := process.Start(); err != nil {
				t.Fatal(err)
			}
			defer process.Process.Kill()
			client := &http.Client{Timeout: 200 * time.Millisecond}
			deadline := time.Now().Add(8 * time.Second)
			ready := false
			for time.Now().Before(deadline) {
				response, err := client.Get("http://" + api.Addr().String() + "/v1/identity")
				if err == nil {
					var identity map[string]any
					_ = json.NewDecoder(response.Body).Decode(&identity)
					response.Body.Close()
					ready = response.StatusCode == 200 && identity["service"] == "nib-handler"
				}
				if ready {
					break
				}
				time.Sleep(50 * time.Millisecond)
			}
			if !ready {
				t.Fatal("command did not start a usable Handler")
			}
			if code := handler.Main([]string{"stop", "-config", cfg.Source}); code != 0 {
				t.Fatal(code)
			}
			if err := process.Wait(); err != nil {
				t.Fatal(err)
			}
		})
	}
}
