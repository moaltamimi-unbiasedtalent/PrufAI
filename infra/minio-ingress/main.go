// Package main is the only network ingress to the local MinIO process.
package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"os/exec"
	"os/signal"
	"strings"
	"syscall"
	"time"
)

const (
	s3Backend       = "127.0.0.1:19000"
	consoleBackend  = "127.0.0.1:19001"
	unsignedTrailer = "STREAMING-UNSIGNED-PAYLOAD-TRAILER"
)

func ingress(addr, backend string) *http.Server {
	target := &url.URL{Scheme: "http", Host: backend}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.Proxy = nil
	transport.DisableCompression = true
	proxy := &httputil.ReverseProxy{
		Transport: transport,
		Rewrite: func(r *httputil.ProxyRequest) {
			r.SetURL(target)
			// S3 signatures bind the original Host and query.
			r.Out.Host = r.In.Host
			r.Out.URL.RawQuery = r.In.URL.RawQuery
		},
	}
	handler := http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Check every parsed value, including duplicate and combined headers,
		// before the proxy can forward a request or read its body.
		for _, value := range r.Header.Values("X-Amz-Content-Sha256") {
			if strings.Contains(strings.ToUpper(value), unsignedTrailer) {
				w.Header().Set("X-Prufai-Storage-Guard", "unsigned-trailer-denied")
				w.Header().Set("Content-Type", "application/xml")
				w.WriteHeader(http.StatusForbidden)
				_, _ = w.Write([]byte("<Error><Code>AccessDenied</Code><Message>Unsigned trailer uploads are disabled</Message></Error>"))
				return
			}
		}
		proxy.ServeHTTP(w, r)
	})
	return &http.Server{Addr: addr, Handler: handler, ReadHeaderTimeout: 10 * time.Second}
}

func run() error {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	servers := []*http.Server{ingress(":9000", s3Backend), ingress(":9001", consoleBackend)}
	listeners := make([]net.Listener, len(servers))
	for i, server := range servers {
		listener, err := net.Listen("tcp", server.Addr)
		if err != nil {
			return err
		}
		defer listener.Close()
		listeners[i] = listener
	}

	// Fixed loopback addresses keep both MinIO listeners off the bridge
	// network. The embedded Console also uses the guarded S3 listener.
	if err := os.Setenv("MINIO_SERVER_URL", "http://127.0.0.1:9000"); err != nil {
		return err
	}
	child := exec.Command("/usr/local/bin/minio", "server", "/data",
		"--address", s3Backend, "--console-address", consoleBackend)
	child.Stdout, child.Stderr = os.Stdout, os.Stderr
	if err := child.Start(); err != nil {
		return err
	}

	childDone := make(chan error, 1)
	go func() { childDone <- child.Wait() }()
	serverDone := make(chan error, len(servers))
	for i, server := range servers {
		go func() { serverDone <- server.Serve(listeners[i]) }()
	}

	var serveErr error
	select {
	case <-ctx.Done():
	case err := <-serverDone:
		serveErr = fmt.Errorf("storage ingress stopped: %w", err)
	case err := <-childDone:
		return fmt.Errorf("MinIO stopped unexpectedly: %v", err)
	}

	for _, server := range servers {
		_ = server.Close()
	}
	if err := child.Process.Signal(syscall.SIGTERM); err != nil && !errors.Is(err, os.ErrProcessDone) {
		serveErr = errors.Join(serveErr, err)
	}
	select {
	case <-childDone:
	case <-time.After(8 * time.Second):
		_ = child.Process.Kill()
		<-childDone
		serveErr = errors.Join(serveErr, errors.New("MinIO shutdown timed out"))
	}
	return serveErr
}

func main() {
	if err := run(); err != nil {
		log.Fatal(err)
	}
}
