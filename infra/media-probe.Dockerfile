# Signed Ubuntu packages and a pinned multi-architecture base. Build once; audio
# inspection runs offline, read-only, without capabilities and with hard limits.
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg=7:6.1.1-3ubuntu5 \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir /input
USER 65534:65534
ENTRYPOINT ["/usr/bin/ffprobe"]
