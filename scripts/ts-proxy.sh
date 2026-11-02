#!/bin/bash
# ProxyCommand helper: connect to $1:$2 through the runtime egress proxy on port 3130 (Tailscale tunnel).
# Proxy credentials are read from HTTPS_PROXY at runtime; nothing secret is stored in this file.
auth=$(python3 -c "from urllib.parse import urlparse; import os; p = urlparse(os.environ['HTTPS_PROXY']); print(p.username + ':' + p.password)")
exec socat - "PROXY:hatch-egress-proxy:$1:$2,proxyport=3130,proxyauth=$auth"
