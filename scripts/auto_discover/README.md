# FluxChi Listener — Mac Auto-Discovery

This wrapper makes `lelamp-fluxchi-listener.service` find the FluxChi Mac
backend automatically on every start. Without it, the listener has a
hardcoded `FLUXCHI_WS=ws://<MAC_IP>:8000/ws/harness` in the systemd env
file, which goes stale every time the Mac moves to a new Wi-Fi network.

## What it solves

* Mac switches Wi-Fi → IP changes → listener can no longer connect.
* No mDNS / Bonjour on this Raspberry Pi OS image (avahi not installed,
  nsswitch.conf doesn't have `mdns_minimal`).
* AP isolation on shared networks (corporate, hotel) breaks Tailscale
  + LAN multicast discovery.

## How it works

```
systemd start lelamp-fluxchi-listener.service
  └─ ExecStart=fluxchi_listener_wrap.sh
       └─ python3 find_fluxchi_mac.py
            1. try cached IP from ~/.cache/fluxchi/mac_ip
            2. scan Pi's own /24                (~5s, ~250 IPs)
            3. scan Pi's /20                    (~30s, ~4k IPs)
            4. scan rest of Pi's /16            (~3 min, ~60k IPs)
       └─ exec listener --ws ws://FOUND:8000/...
```

If discovery fails, the wrapper exits and `Restart=always` cycles it
every 3 s. Once Mac comes online, the next probe finds it and caches.

## Install

From a clean `lelamp_runtime` checkout on the Pi:

```bash
cd ~/lelamp-dev/lelamp_runtime_canary_<sha>/
sudo scripts/install_fluxchi_auto_discover.sh
```

After install, the systemd drop-in
`/etc/systemd/system/lelamp-fluxchi-listener.service.d/fluxchi-ws.conf`
overrides `ExecStart` to invoke the wrapper.

## Verify

```bash
sudo journalctl -u lelamp-fluxchi-listener -f
```

Expected on first start:
```
fluxchi_listener_wrap.sh: [fluxchi-wrap] resolving Mac...
fluxchi_listener_wrap.sh: [fluxchi-wrap] found Mac at 10.x.y.z, launching listener
fluxchi_listener: connected session_id=sess_...
```

Subsequent starts hit the cache:
```
[resolve] trying cache: 10.x.y.z
[fluxchi-wrap] found Mac at 10.x.y.z, launching listener   # < 1 s
```

## Operate

```bash
# Force re-scan (clear cache)
rm ~/.cache/fluxchi/mac_ip
sudo systemctl restart lelamp-fluxchi-listener

# Tail logs
sudo journalctl -u lelamp-fluxchi-listener -f

# Stop / disable
sudo systemctl stop    lelamp-fluxchi-listener
sudo systemctl disable lelamp-fluxchi-listener

# Re-enable
sudo systemctl enable --now lelamp-fluxchi-listener
```

## Uninstall

```bash
sudo rm /etc/systemd/system/lelamp-fluxchi-listener.service.d/fluxchi-ws.conf
sudo rm /usr/local/lib/lelamp/find_fluxchi_mac.py
sudo rm /usr/local/lib/lelamp/fluxchi_listener_wrap.sh
sudo systemctl daemon-reload
sudo systemctl restart lelamp-fluxchi-listener
```

(Note: the listener will then go back to using whatever `FLUXCHI_WS`
is in `/etc/default/lelamp-fluxchi-listener`, which is what
`install_fluxchi_sidecars.sh` originally set.)
