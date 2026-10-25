#!/bin/sh
# Run once on the Oracle Cloud server (Ubuntu), inside the unpacked engine folder:
#     sh oracle_setup.sh
# Installs Docker, opens ports 80 and 443, builds the engine image and starts it behind
# Caddy, which gets a free HTTPS certificate for <ip>.sslip.io (a name that points at
# the server's own address, no domain needed). Both restart by themselves after a crash
# or a reboot. Run it again after uploading new code.
set -e

if ! command -v docker >/dev/null; then
    sudo apt-get update
    sudo apt-get install -y docker.io
fi

# Oracle's Ubuntu images block every port but SSH in their own firewall, on top of the
# cloud's security list.
for port in 80 443; do
    if ! sudo iptables -C INPUT -p tcp --dport $port -j ACCEPT 2>/dev/null; then
        sudo iptables -I INPUT 6 -p tcp --dport $port -j ACCEPT
        changed=1
    fi
done
if [ -n "$changed" ]; then
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent
    sudo netfilter-persistent save
fi

# Building the model inside the image takes several minutes the first time.
sudo docker build -t drainout-engine .
sudo docker rm -f drainout-engine caddy 2>/dev/null || true
sudo docker run -d --name drainout-engine --restart unless-stopped -p 127.0.0.1:7860:7860 \
    ${OPENWEATHER_API_KEY:+-e OPENWEATHER_API_KEY=$OPENWEATHER_API_KEY} drainout-engine

host="$(curl -s https://api.ipify.org | tr . -).sslip.io"
sudo docker run -d --name caddy --restart unless-stopped --network host -v caddy_data:/data \
    caddy:2 caddy reverse-proxy --from "$host" --to localhost:7860

echo "Engine address: https://$host"
echo "Ready when this answers (a few minutes): curl https://$host/data-collection/engine"
