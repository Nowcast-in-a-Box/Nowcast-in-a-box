// Package handler is the Nowcast-in-a-Box host daemon.
//
// It binds loopback HTTP, checks Docker, and will start Model Containers
// through Compose. Metrics and visualization run in the diagnostics
// container, started by `nib-handler diagnostics`, not on the host.
// Bind addresses and paths come from config.yaml.
package handler
