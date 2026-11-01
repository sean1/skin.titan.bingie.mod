// Package main exposes a small C ABI for BINGIE's private WireGuard proxy.
// The library must remain loaded for the lifetime of the hosting process.
package main

/* */
import "C"

import (
	"context"
	"crypto/ecdh"
	"crypto/rand"
	"crypto/subtle"
	"encoding/base64"
	"fmt"
	"io"
	"log"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
	"unsafe"

	"github.com/pufferffish/wireproxy"
	"golang.zx2c4.com/wireguard/conn"
	"golang.zx2c4.com/wireguard/device"
	"golang.zx2c4.com/wireguard/tun/netstack"
)

// The C API returns only status codes. Neither config errors nor requests are logged.
type vpnProxy struct {
	server      *http.Server
	tun         *netstack.Net
	device      *device.Device
	cancel      context.CancelFunc
	auth        string
	mu          sync.Mutex
	closed      bool
	connections map[net.Conn]struct{}
	handlers    sync.WaitGroup
	done        chan struct{}
}

var lifecycle sync.Mutex
var active *vpnProxy

// GenerateKeyPair writes a base64 WireGuard key pair into two separate,
// caller-owned buffers. Each buffer must hold at least 45 bytes including NUL.
// It returns 0 on success, 1 for invalid buffers, and 4 on internal failure.
//
//export GenerateKeyPair
func GenerateKeyPair(privateKey *C.char, privateLen C.size_t, publicKey *C.char, publicLen C.size_t) (result C.int) {
	defer func() {
		if recover() != nil {
			result = 4
		}
	}()
	if privateKey == nil || publicKey == nil || privateLen < 45 || publicLen < 45 {
		return 1
	}
	privateAddress, publicAddress := uintptr(unsafe.Pointer(privateKey)), uintptr(unsafe.Pointer(publicKey))
	if (privateAddress <= publicAddress && publicAddress-privateAddress < 45) || (publicAddress < privateAddress && privateAddress-publicAddress < 45) {
		return 1
	}
	privateBuffer := unsafe.Slice((*byte)(unsafe.Pointer(privateKey)), 45)
	publicBuffer := unsafe.Slice((*byte)(unsafe.Pointer(publicKey)), 45)
	for i := range privateBuffer {
		privateBuffer[i] = 0
		publicBuffer[i] = 0
	}
	key, err := ecdh.X25519().GenerateKey(rand.Reader)
	if err != nil {
		return 4
	}
	privateBytes := key.Bytes()
	defer func() {
		for i := range privateBytes {
			privateBytes[i] = 0
		}
	}()
	base64.StdEncoding.Encode(privateBuffer[:44], privateBytes)
	base64.StdEncoding.Encode(publicBuffer[:44], key.PublicKey().Bytes())
	return 0
}

// startDevice owns and closes every resource allocated before a successful start.
func startDevice(conf *wireproxy.DeviceConfig) (result *device.Device, network *netstack.Net, err error) {
	tun, network, err := netstack.CreateNetTUN(conf.Endpoint, conf.DNS, conf.MTU)
	if err != nil {
		return nil, nil, err
	}
	var dev *device.Device
	committed := false
	defer func() {
		if !committed {
			if dev != nil {
				dev.Close()
			} else {
				_ = tun.Close()
			}
		}
	}()
	dev = device.NewDevice(tun, conn.NewDefaultBind(), device.NewLogger(device.LogLevelSilent, ""))
	var settings strings.Builder
	fmt.Fprintf(&settings, "private_key=%s\n", conf.SecretKey)
	if conf.ListenPort != nil {
		fmt.Fprintf(&settings, "listen_port=%d\n", *conf.ListenPort)
	}
	for _, peer := range conf.Peers {
		fmt.Fprintf(&settings, "public_key=%s\npersistent_keepalive_interval=%d\npreshared_key=%s\n", peer.PublicKey, peer.KeepAlive, peer.PreSharedKey)
		if peer.Endpoint != nil {
			fmt.Fprintf(&settings, "endpoint=%s\n", *peer.Endpoint)
		}
		if len(peer.AllowedIPs) == 0 {
			settings.WriteString("allowed_ip=0.0.0.0/0\nallowed_ip=::/0\n")
		} else {
			for _, ip := range peer.AllowedIPs {
				fmt.Fprintf(&settings, "allowed_ip=%s\n", ip.String())
			}
		}
	}
	if err = dev.IpcSet(settings.String()); err != nil {
		return nil, nil, err
	}
	if err = dev.Up(); err != nil {
		return nil, nil, err
	}
	committed = true
	return dev, network, nil
}

func (p *vpnProxy) beginHandler() bool {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.closed {
		return false
	}
	p.handlers.Add(1)
	return true
}

func (p *vpnProxy) closeConnections() {
	p.mu.Lock()
	p.closed = true
	for conn := range p.connections {
		_ = conn.Close()
	}
	p.mu.Unlock()
}

func (p *vpnProxy) serveHTTP(w http.ResponseWriter, r *http.Request) {
	if !p.beginHandler() {
		http.Error(w, "Unavailable", http.StatusServiceUnavailable)
		return
	}
	defer p.handlers.Done()
	if r.Method != http.MethodConnect || subtle.ConstantTimeCompare([]byte(r.Header.Get("Proxy-Authorization")), []byte(p.auth)) != 1 {
		http.Error(w, "Unavailable", http.StatusForbidden)
		return
	}
	host, port, err := net.SplitHostPort(r.Host)
	if err != nil || host == "" {
		http.Error(w, "Unavailable", http.StatusBadRequest)
		return
	}
	n, err := strconv.Atoi(port)
	if err != nil || n < 1 || n > 65535 {
		http.Error(w, "Unavailable", http.StatusBadRequest)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 20*time.Second)
	defer cancel()
	upstream, err := p.tun.DialContext(ctx, "tcp", net.JoinHostPort(host, port))
	if err != nil {
		http.Error(w, "Unavailable", http.StatusBadGateway)
		return
	}
	hijacker, ok := w.(http.Hijacker)
	if !ok {
		_ = upstream.Close()
		http.Error(w, "Unavailable", http.StatusInternalServerError)
		return
	}
	client, buffered, err := hijacker.Hijack()
	if err != nil {
		_ = upstream.Close()
		return
	}
	p.mu.Lock()
	if p.closed {
		p.mu.Unlock()
		_ = client.Close()
		_ = upstream.Close()
		return
	}
	p.connections[client] = struct{}{}
	p.connections[upstream] = struct{}{}
	p.mu.Unlock()
	defer func() {
		_ = client.Close()
		_ = upstream.Close()
		p.mu.Lock()
		delete(p.connections, client)
		delete(p.connections, upstream)
		p.mu.Unlock()
	}()
	if _, err = buffered.WriteString("HTTP/1.1 200 Connection Established\r\n\r\n"); err != nil {
		return
	}
	if err = buffered.Flush(); err != nil {
		return
	}
	done := make(chan struct{})
	go func() {
		defer close(done)
		defer client.Close()
		defer upstream.Close()
		_, _ = io.Copy(upstream, buffered)
	}()
	_, _ = io.Copy(client, upstream)
	_ = client.Close()
	_ = upstream.Close()
	<-done
}

// Start returns 0 on success, 1 for invalid input/configuration, 2 if already
// started, 3 on a listener error, and 4 when the WireGuard device cannot start.
//
//export Start
func Start(configPath *C.char, password *C.char, port C.int) (result C.int) {
	var listener net.Listener
	var dev *device.Device
	committed := false
	defer func() {
		if recover() != nil {
			result = 4
		}
		if !committed {
			if listener != nil {
				_ = listener.Close()
			}
			if dev != nil {
				dev.Close()
			}
		}
	}()
	if configPath == nil || password == nil || port < 1 || port > 65535 {
		return 1
	}
	path, secret := C.GoString(configPath), C.GoString(password)
	if path == "" || len(secret) < 24 {
		return 1
	}
	lifecycle.Lock()
	defer lifecycle.Unlock()
	if active != nil {
		return 2
	}
	config, err := wireproxy.ParseConfig(path)
	if err != nil || config == nil || config.Device == nil || len(config.Device.DNS) == 0 || len(config.Device.Peers) != 1 || config.Device.Peers[0].Endpoint == nil {
		return 1
	}
	listener, err = net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", int(port)))
	if err != nil {
		return 3
	}
	var network *netstack.Net
	dev, network, err = startDevice(config.Device)
	if err != nil {
		return 4
	}
	ctx, cancel := context.WithCancel(context.Background())
	p := &vpnProxy{tun: network, device: dev, cancel: cancel, auth: "Basic " + base64.StdEncoding.EncodeToString([]byte("bingie:"+secret)), connections: make(map[net.Conn]struct{}), done: make(chan struct{})}
	p.server = &http.Server{Handler: http.HandlerFunc(p.serveHTTP), ReadHeaderTimeout: 5 * time.Second, MaxHeaderBytes: 8192, ErrorLog: log.New(io.Discard, "", 0), BaseContext: func(net.Listener) context.Context { return ctx }}
	active = p
	committed = true
	go func() { defer close(p.done); defer func() { _ = recover() }(); _ = p.server.Serve(listener) }()
	return 0
}

// Status returns 0 when stopped, 1 when started without a completed
// handshake, 2 after a handshake, and 3 if state cannot be read. A past
// handshake is not proof that the tunnel is currently working.
//
//export Status
func Status() (result C.int) {
	defer func() {
		if recover() != nil {
			result = 3
		}
	}()
	lifecycle.Lock()
	defer lifecycle.Unlock()
	if active == nil {
		return 0
	}
	select {
	case <-active.done:
		return 3
	default:
	}
	metrics, err := active.device.IpcGet()
	if err != nil {
		return 3
	}
	for _, line := range strings.Split(metrics, "\n") {
		if strings.HasPrefix(line, "last_handshake_time_sec=") {
			stamp, err := strconv.ParseInt(strings.TrimPrefix(line, "last_handshake_time_sec="), 10, 64)
			if err != nil {
				return 3
			}
			if stamp > 0 {
				return 2
			}
			return 1
		}
	}
	return 3
}

// Stop closes the listener, active connections, handlers and WireGuard device.
//
//export Stop
func Stop() (result C.int) {
	defer func() {
		if recover() != nil {
			result = 4
		}
	}()
	lifecycle.Lock()
	defer lifecycle.Unlock()
	if active == nil {
		return 0
	}
	p := active
	p.cancel()
	_ = p.server.Close()
	p.closeConnections()
	p.device.Close()
	p.handlers.Wait()
	<-p.done
	active = nil
	return 0
}

func main() {}
