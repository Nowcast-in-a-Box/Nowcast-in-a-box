//go:build darwin

package handler

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
)

// Called while the lifecycle lock is held, only for a newly activated install.
func registerInstall(cfg Config) error {
	if os.Getenv("NIB_SKIP_URI_REGISTRATION") == "1" || cfg.Source == "" {
		return nil
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	app := filepath.Join(home, "Applications", "NiB Handler.app")
	if err := os.MkdirAll(filepath.Dir(app), 0700); err != nil {
		return err
	}
	temp, err := os.MkdirTemp(filepath.Dir(app), ".nib-registration-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(temp)
	staged := filepath.Join(temp, "NiB Handler.app")
	script := filepath.Join(temp, "handler.applescript")
	base := fmt.Sprintf("(quoted form of %s) & \" %%s -config \" & (quoted form of %s) & \" -host \" & (quoted form of %s) & \" -port \" & (quoted form of %s)", strconv.Quote(executable), strconv.Quote(cfg.Source), strconv.Quote(cfg.Handler.Host), strconv.Quote(strconv.Itoa(cfg.Handler.Port)))
	program := "on run\n do shell script " + fmt.Sprintf(base, "start") + "\nend run\non open location theURL\n do shell script " + fmt.Sprintf(base, "connect") + " & \" \" & (quoted form of theURL)\nend open location\n"
	if err := os.WriteFile(script, []byte(program), 0600); err != nil {
		return err
	}
	run := func(name string, args ...string) error {
		out, err := exec.Command(name, args...).CombinedOutput()
		if err != nil {
			return fmt.Errorf("register Handler app: %w: %s", err, out)
		}
		return nil
	}
	if err := run("/usr/bin/osacompile", "-o", staged, script); err != nil {
		return err
	}
	plist := filepath.Join(staged, "Contents", "Info.plist")
	if err := exec.Command("/usr/libexec/PlistBuddy", "-c", "Set :CFBundleIdentifier app.nowcast.nib-handler", plist).Run(); err != nil {
		if err := run("/usr/libexec/PlistBuddy", "-c", "Add :CFBundleIdentifier string app.nowcast.nib-handler", plist); err != nil {
			return err
		}
	}
	if err := run("/usr/libexec/PlistBuddy", "-c", "Add :CFBundleURLTypes array", "-c", "Add :CFBundleURLTypes:0 dict", "-c", "Add :CFBundleURLTypes:0:CFBundleURLSchemes array", "-c", "Add :CFBundleURLTypes:0:CFBundleURLSchemes:0 string nib", plist); err != nil {
		return err
	}
	if err := run("/usr/bin/codesign", "--force", "--sign", "-", staged); err != nil {
		return err
	}
	if err := removeInstallRegistration(); err != nil {
		return err
	}
	if err := os.Rename(staged, app); err != nil {
		return err
	}
	return run("/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister", "-f", app)
}
