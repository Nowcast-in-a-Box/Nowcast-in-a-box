package main

import (
	"fmt"
	"net"
	"strings"
)

func requireLoopbackHost(host string) error {
	if host == "localhost" {
		return nil
	}
	ip := net.ParseIP(host)
	if ip != nil {
		if ip.IsLoopback() {
			return nil
		}
		return fmt.Errorf(
			"refusing to bind non-loopback host %q; the handler must listen only on localhost (e.g. 127.0.0.1)",
			host,
		)
	}
	addrs, err := net.LookupIP(host)
	if err != nil || len(addrs) == 0 {
		return fmt.Errorf("cannot resolve bind host %q", host)
	}
	var bad []string
	for _, addr := range addrs {
		if !addr.IsLoopback() {
			bad = append(bad, addr.String())
		}
	}
	if len(bad) > 0 {
		return fmt.Errorf(
			"refusing to bind non-loopback host %q (resolves to %s); the handler must listen only on localhost (e.g. 127.0.0.1)",
			host,
			strings.Join(bad, ", "),
		)
	}
	return nil
}

func isLoopbackRequestHost(hostport string) bool {
	host, _, err := net.SplitHostPort(hostport)
	if err != nil {
		host = hostport
	}
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}
