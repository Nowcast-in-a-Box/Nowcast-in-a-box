//go:build darwin

package handler

import (
	"bytes"
	"context"
	"encoding/xml"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"time"
)

func launchctl(args ...string) (string, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	output, err := exec.CommandContext(ctx, "/bin/launchctl", args...).CombinedOutput()
	if err != nil {
		return string(output), fmt.Errorf("launchctl %s: %w: %s", args[0], err, strings.TrimSpace(string(output)))
	}
	return string(output), nil
}

func backgroundProcessExited(cfg Config, pid int) bool {
	target := "gui/" + strconv.Itoa(os.Getuid()) + "/app.nowcast.nib-handler." + profileID(cfg)[:16]
	output, err := launchctl("print", target)
	if err != nil {
		var exit *exec.ExitError
		return errors.As(err, &exit) && exit.ExitCode() == 113 // No registered job.
	}
	for _, line := range strings.Split(output, "\n") {
		entry := strings.TrimSpace(line)
		if strings.HasPrefix(entry, "pid = ") && (pid == 0 || entry == "pid = "+strconv.Itoa(pid)) {
			return false
		}
	}
	return true
}

func plistString(value string) string {
	var escaped bytes.Buffer
	_ = xml.EscapeText(&escaped, []byte(value))
	return "<string>" + escaped.String() + "</string>"
}

func startBackground(cfg Config) error {
	if cfg.Source == "" {
		return fmt.Errorf("background startup requires a config file; use -config /path/to/config.yaml")
	}
	directory, err := sessionDirectory(cfg)
	if err != nil {
		return err
	}
	if err := os.MkdirAll(directory, 0o700); err != nil {
		return err
	}
	lock, err := os.OpenFile(filepath.Join(directory, "start.lock"), os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return err
	}
	defer lock.Close()
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX); err != nil {
		return err
	}
	defer syscall.Flock(int(lock.Fd()), syscall.LOCK_UN)
	// Another simultaneous launcher may have completed startup while we waited.
	needsStart, err := prepareStart(cfg)
	if err != nil || !needsStart {
		return err
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	executable = canonicalPath(executable)
	label := "app.nowcast.nib-handler." + profileID(cfg)[:16]
	target := "gui/" + strconv.Itoa(os.Getuid()) + "/" + label
	if output, err := launchctl("print", target); err == nil {
		for _, line := range strings.Split(output, "\n") {
			if strings.HasPrefix(strings.TrimSpace(line), "pid = ") {
				return fmt.Errorf("registered Handler is still running; stop it with its local CLI before retrying")
			}
		}
		if _, err := launchctl("bootout", target); err != nil {
			return err
		}
	}
	var definition strings.Builder
	definition.WriteString("<?xml version=\"1.0\" encoding=\"UTF-8\"?><plist version=\"1.0\"><dict>")
	definition.WriteString("<key>Label</key>" + plistString(label))
	definition.WriteString("<key>ProgramArguments</key><array>")
	for _, arg := range []string{executable, "serve-child", "-config", cfg.Source, "-host", cfg.Handler.Host, "-port", strconv.Itoa(cfg.Handler.Port)} {
		definition.WriteString(plistString(arg))
	}
	definition.WriteString("</array><key>WorkingDirectory</key>" + plistString(filepath.Dir(cfg.Source)))
	definition.WriteString("<key>RunAtLoad</key><true/><key>KeepAlive</key><false/>")
	definition.WriteString("<key>ProcessType</key><string>Interactive</string><key>Umask</key><integer>63</integer>")
	definition.WriteString("<key>StandardOutPath</key>" + plistString(filepath.Join(directory, "handler.log")))
	definition.WriteString("<key>StandardErrorPath</key>" + plistString(filepath.Join(directory, "handler.log")))
	definition.WriteString("<key>EnvironmentVariables</key><dict>")
	for _, name := range []string{"HOME", "PATH", "NIB_DATA_ROOT", "DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"} {
		value := os.Getenv(name)
		if name == "PATH" {
			// Finder's environment may omit Docker Desktop/Homebrew command paths.
			value += ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
		}
		if value != "" {
			definition.WriteString("<key>" + name + "</key>" + plistString(value))
		}
	}
	definition.WriteString("</dict></dict></plist>")
	plist := filepath.Join(directory, "handler.plist")
	if err := os.WriteFile(plist, []byte(definition.String()), 0o600); err != nil {
		return err
	}
	// This file is outside ~/Library/LaunchAgents: it is loaded only on demand,
	// never automatically at login. launchd owns the process, not the terminal.
	if _, err := launchctl("bootstrap", "gui/"+strconv.Itoa(os.Getuid()), plist); err != nil {
		return fmt.Errorf("%w: %v", errBackgroundUnavailable, err)
	}
	ctx, cancel := context.WithTimeout(context.Background(), 8*time.Second)
	defer cancel()
	for {
		var identity identityResponse
		identity, err = probeInstance(ctx, cfg)
		if err == nil && identity.State == "running" {
			return nil
		}
		select {
		case <-ctx.Done():
			return fmt.Errorf("Handler startup was not confirmed: %v; inspect %s; use stop before retrying if it is running", err, filepath.Join(directory, "handler.log"))
		case <-time.After(100 * time.Millisecond):
		}
	}
}
