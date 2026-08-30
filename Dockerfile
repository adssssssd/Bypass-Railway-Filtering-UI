# Xray VLESS+WS — Railway auto-install image
# Installs xray the same way a manual install does:
#   download linux-64 zip from Xray-core GitHub releases -> /usr/local/bin/xray

FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ---- install xray exactly like the manual setup ----
ARG XRAY_VERSION=latest
RUN set -eux; \
    if [ "$XRAY_VERSION" = "latest" ]; then \
      XRAY_VERSION=$(curl -sS https://api.github.com/repos/XTLS/Xray-core/releases/latest | grep -oP '"tag_name":\s*"\K[^"]+' | head -1); \
    fi; \
    echo "Installing Xray $XRAY_VERSION"; \
    curl -sSL -o /tmp/xray.zip "https://github.com/XTLS/Xray-core/releases/download/${XRAY_VERSION}/Xray-linux-64.zip"; \
    unzip -o /tmp/xray.zip -d /tmp/xray -x "*.pdf" "LICENSE" "README*"; \
    install -m 755 /tmp/xray/xray /usr/local/bin/xray; \
    rm -rf /tmp/xray.zip /tmp/xray; \
    xray version

COPY backend/router.py .
COPY backend/xray/config.template.json /app/config.template.json
COPY backend/entrypoint.sh .
RUN chmod +x entrypoint.sh

EXPOSE 8080
ENTRYPOINT ["./entrypoint.sh"]