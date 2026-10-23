package handler_test

import (
	"encoding/json"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"testing"
	"time"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
	"gopkg.in/yaml.v3"
)

func TestServeCLIChild(t *testing.T) {
	if os.Getenv("NIB_TEST_SERVE_CHILD") != "1" {
		return
	}
	for i, argument := range os.Args {
		if argument == "--" {
			os.Exit(handler.Main(os.Args[i+1:]))
		}
	}
	os.Exit(2)
}

func isolatedInstall(t *testing.T) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("HOME", home)
	t.Setenv("XDG_CACHE_HOME", filepath.Join(home, ".cache"))
	t.Setenv("LOCALAPPDATA", filepath.Join(home, "AppData", "Local"))
}

func writeInstallConfig(t *testing.T) handler.Config {
	t.Helper()
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
	cfg.Paths.Resources = testResourcesPath(t)
	cfg.Source = filepath.Join(t.TempDir(), "config.yaml")
	api.Close()
	review.Close()
	writeInstallSettings(t, cfg, "auto")
	raw, err := yaml.Marshal(cfg)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cfg.Source, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	return cfg
}

func writeInstallSettings(t *testing.T, cfg handler.Config, imageSource string) {
	t.Helper()
	path := filepath.Join(filepath.Dir(cfg.Source), "nib-settings.json")
	if err := os.WriteFile(path, []byte(`{"image_source":"`+imageSource+`"}`), 0o600); err != nil {
		t.Fatal(err)
	}
}

func serveInTerminal(t *testing.T, cfg handler.Config) *exec.Cmd {
	t.Helper()
	if runtime.GOOS != "windows" {
		if _, err := exec.LookPath("lsof"); err != nil {
			if _, err := exec.LookPath("ss"); err != nil {
				t.Skip("needs lsof or ss to locate the listening process")
			}
		}
	}
	process := exec.Command(os.Args[0], "-test.run=^TestServeCLIChild$", "--", "serve", "-config", cfg.Source)
	process.Env = append(os.Environ(), "NIB_TEST_SERVE_CHILD=1")
	if err := process.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = process.Process.Kill()
		_, _ = process.Process.Wait()
	})
	waitForIdentity(t, cfg)
	return process
}

func waitForIdentity(t *testing.T, cfg handler.Config) {
	t.Helper()
	address := "http://" + net.JoinHostPort(cfg.Handler.Host, strconv.Itoa(cfg.Handler.Port)) + "/v1/identity"
	client := &http.Client{Timeout: 200 * time.Millisecond}
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		response, err := client.Get(address)
		if err == nil {
			var identity map[string]any
			_ = json.NewDecoder(response.Body).Decode(&identity)
			response.Body.Close()
			if response.StatusCode == http.StatusOK && identity["service"] == "nib-handler" {
				return
			}
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatal("serve child never answered /v1/identity")
}

func waitProcessExit(t *testing.T, process *exec.Cmd) {
	t.Helper()
	done := make(chan struct{})
	go func() {
		_, _ = process.Process.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("Handler survived stop")
	}
}

// An uninstaller deletes the session file before it stops the Handler, and an
// upgraded binary cannot authenticate an older instance. Both leave a Handler
// serving the configured port with nothing to authenticate against.
func TestStopEndsHandlerWithoutSession(t *testing.T) {
	isolatedInstall(t)
	cfg := writeInstallConfig(t)
	process := serveInTerminal(t, cfg)
	cache, err := os.UserCacheDir()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.RemoveAll(filepath.Join(cache, "NiB")); err != nil {
		t.Fatal(err)
	}
	if code := handler.Main([]string{"stop", "-config", cfg.Source}); code == 0 {
		t.Fatal("plain stop ended a Handler it could not authenticate")
	}
	if code := handler.Main([]string{"stop", "-force", "-config", cfg.Source}); code != 0 {
		t.Fatalf("stop -force exit=%d", code)
	}
	waitProcessExit(t, process)
}

// The Hub saves the image-source preference next to config.yaml while the
// Handler runs. That preference must not invalidate the running identity.
func TestStopToleratesChangedImageSourcePreference(t *testing.T) {
	isolatedInstall(t)
	cfg := writeInstallConfig(t)
	writeInstallSettings(t, cfg, "mirror")
	process := serveInTerminal(t, cfg)
	writeInstallSettings(t, cfg, "official")
	if code := handler.Main([]string{"stop", "-config", cfg.Source}); code != 0 {
		t.Fatalf("stop exit=%d", code)
	}
	waitProcessExit(t, process)
}
