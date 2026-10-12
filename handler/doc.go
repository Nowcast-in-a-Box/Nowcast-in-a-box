// Package handler is the Nowcast-in-a-Box host daemon.
//
// It binds loopback HTTP, checks Docker, and will start Model Containers
// through Compose. It does not run inference or scientific diagnostics.
// Bind addresses and paths come from config.yaml.
package handler
