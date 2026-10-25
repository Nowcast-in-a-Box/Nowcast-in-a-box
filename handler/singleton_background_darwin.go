//go:build darwin

package handler

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

func removeBackgroundRegistration(session localSession) error {
	root, err := sessionDirectory(Config{})
	if err != nil {
		return err
	}
	if session.LegacyPath != "" {
		root = filepath.Dir(session.LegacyPath)
	}
	raw, err := os.ReadFile(filepath.Join(root, "handler.plist"))
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		return err
	}
	text := string(raw)
	const prefix = "<key>Label</key><string>"
	start := strings.Index(text, prefix)
	if start < 0 {
		return nil
	}
	label, _, ok := strings.Cut(text[start+len(prefix):], "</string>")
	if !ok || !strings.HasPrefix(label, "app.nowcast.nib-handler.") {
		return nil
	}
	target := "gui/" + strconv.Itoa(os.Getuid()) + "/" + label
	if _, err := launchctl("print", target); err == nil {
		if _, err := launchctl("bootout", target); err != nil {
			return err
		}
	}
	return os.Remove(filepath.Join(root, "handler.plist"))
}

func retireIdleServices() error {
	if os.Getenv("NIB_SKIP_URI_REGISTRATION") == "1" {
		return nil
	}
	output, err := launchctl("list")
	if err != nil {
		return err
	}
	for _, line := range strings.Split(output, "\n") {
		fields := strings.Fields(line)
		if len(fields) != 3 || !strings.HasPrefix(fields[2], "app.nowcast.nib-handler.") {
			continue
		}
		if fields[0] != "-" {
			pid, err := strconv.Atoi(fields[0])
			if err != nil || !processExited(pid) {
				return fmt.Errorf("another Handler service is still running; refusing to remove its registration")
			}
		}
		if _, err := launchctl("bootout", "gui/"+strconv.Itoa(os.Getuid())+"/"+fields[2]); err != nil {
			return err
		}
	}
	return nil
}
