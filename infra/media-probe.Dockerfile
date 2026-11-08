# Signed Ubuntu packages and a pinned multi-architecture base. Build once; audio
# inspection runs offline, read-only, without capabilities and with hard limits.
# The pinned Ubuntu root filesystem lacks a CA bundle. Reuse the trust roots
# from the existing pinned Go builder before contacting any APT repository.
FROM golang:1.24.13-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac AS ca
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
COPY --from=ca /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
# CVE-2026-109518. Bootstrap over TLS with download retries disabled, then
# verify both installed binaries from the affected Ubuntu source package.
RUN sed -Ei 's!http://((archive|security|ports)\.ubuntu\.com)!https://\1!g' /etc/apt/sources.list.d/ubuntu.sources \
    && ! grep -E '^URIs:.*http://' /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get -o Acquire::Retries=0 -o APT::Update::Error-Mode=any update \
    && apt-get -o Acquire::Retries=0 install -y --no-install-recommends --only-upgrade apt libapt-pkg6.0t64 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' apt)" ge 2.8.4ubuntu0.1 \
    && dpkg --compare-versions "$(dpkg-query -W -f='${Version}' libapt-pkg6.0t64)" ge 2.8.4ubuntu0.1 \
    && apt-get -o Acquire::Retries=0 install -y --no-install-recommends ffmpeg=7:6.1.1-3ubuntu5 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir /input
USER 65534:65534
ENTRYPOINT ["/usr/bin/ffprobe"]
