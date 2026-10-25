//go:build !darwin

package handler

func removeBackgroundRegistration(localSession) error { return nil }

func retireIdleServices() error { return nil }
