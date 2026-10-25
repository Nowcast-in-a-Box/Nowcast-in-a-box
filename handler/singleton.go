package handler

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"time"
)

// Locks live outside the removable cache so uninstall cannot split ownership.
func singletonLock(name string) (func(), error) {
	root, err := os.UserConfigDir()
	if err != nil {
		return nil, err
	}
	root = filepath.Join(root, "NiB", "locks")
	if err := os.MkdirAll(root, 0700); err != nil {
		return nil, err
	}
	return lockFile(filepath.Join(root, name+".lock"))
}

func executableDigest() (string, error) {
	name, err := os.Executable()
	if err != nil {
		return "", err
	}
	f, err := os.Open(name)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err = io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// Legacy records are read only to authenticate and retire pre-singleton services.
func discoverSession(cfg Config) (localSession, error) {
	if s, err := readSession(cfg); err == nil {
		if identity, probeErr := probeAddressTimeout(s.URL); probeErr == nil {
			if identity.InstanceID != s.InstanceID || identity.ConfigID != s.ConfigID || identity.URL != s.URL || identity.ReviewURL != s.ReviewURL {
				return s, fmt.Errorf("local control session is stale; refusing automatic replacement")
			}
			if identity.State != "running" {
				return s, fmt.Errorf("Handler is stopping; wait before retrying")
			}
			return s, nil
		} else if !errors.Is(probeErr, errNotRunning) {
			return s, probeErr
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return localSession{}, err
	}
	root, err := sessionDirectory(cfg)
	if err != nil {
		return localSession{}, err
	}
	paths, err := filepath.Glob(filepath.Join(root, "*", "session.json"))
	if err != nil {
		return localSession{}, err
	}
	var found *localSession
	for _, path := range paths {
		info, err := os.Stat(path)
		if err != nil || info.Mode().Perm()&0077 != 0 {
			continue
		}
		raw, err := os.ReadFile(path)
		if err != nil {
			return localSession{}, err
		}
		var s localSession
		if json.Unmarshal(raw, &s) != nil || !localServiceURL(s.URL) || !localServiceURL(s.ReviewURL) || s.Token == "" || s.PID <= 0 {
			continue
		}
		identity, err := probeAddressTimeout(s.URL)
		if errors.Is(err, errNotRunning) {
			continue
		}
		if err != nil {
			return s, err
		}
		if identity.InstanceID != s.InstanceID || identity.ConfigID != s.ConfigID {
			return s, fmt.Errorf("legacy Handler control record is stale; refusing automatic replacement")
		}
		if found != nil && found.InstanceID != s.InstanceID {
			return s, fmt.Errorf("multiple legacy Handlers are running; stop them before upgrading")
		}
		s.LegacyPath = path
		found = &s
	}
	if found != nil {
		return *found, nil
	}
	return localSession{}, os.ErrNotExist
}

func probeAddressTimeout(address string) (identityResponse, error) {
	return probeAddress(context.Background(), address)
}

func retireBackground(session localSession) error {
	if err := removeBackgroundRegistration(session); err != nil {
		return err
	}
	if session.LegacyPath != "" {
		err := os.Remove(session.LegacyPath)
		if !errors.Is(err, os.ErrNotExist) {
			return err
		}
	}
	return nil
}

// A new executable replaces the old service only after authenticated idle stop.
func ensureSingleton(cfg Config) (bool, error) {
	s, err := discoverSession(cfg)
	if errors.Is(err, os.ErrNotExist) {
		return prepareStartAddress(cfg)
	}
	if err != nil {
		return false, err
	}
	digest, err := executableDigest()
	if err != nil {
		return false, err
	}
	_, sourceErr := os.Stat(s.ConfigSource)
	if s.BinarySHA256 == digest && !errors.Is(sourceErr, os.ErrNotExist) {
		fmt.Fprintf(os.Stdout, "Handler already running at %s; retaining its active configuration\n", s.URL)
		return false, nil
	}
	if err := stopRecordedInstance(cfg, s); err != nil {
		return false, fmt.Errorf("cannot replace running Handler: %w", err)
	}
	if err := retireBackground(s); err != nil {
		return false, err
	}
	if err := retireIdleServices(); err != nil {
		return false, err
	}
	if err := removeInstallRegistration(); err != nil {
		return false, err
	}
	fmt.Fprintln(os.Stdout, "Previous Handler service removed; starting this build (user data preserved)")
	return true, nil
}

// Used only for a bounded wait by concurrent launchers.
var singletonWait = 15 * time.Second

func uninstallInstance(cfg Config) error {
	if err := stopInstance(cfg, false); err != nil {
		return err
	}
	release, err := singletonLock("running")
	if err != nil {
		return err
	}
	defer release()
	if err := retireIdleServices(); err != nil {
		return err
	}
	if err := removeInstallRegistration(); err != nil {
		return err
	}
	root, err := sessionDirectory(cfg)
	if err != nil {
		return err
	}
	return os.RemoveAll(root)
}
