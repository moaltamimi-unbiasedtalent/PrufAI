# Same exact MinIO source release used by required GitHub integration jobs.
FROM golang:1.24.13-bookworm@sha256:1a6d4452c65dea36aac2e2d606b01b4a029ec90cc1ae53890540ce6173ea77ac AS build
ENV CGO_ENABLED=0
RUN go install github.com/minio/minio@RELEASE.2025-10-15T17-29-55Z
COPY minio-ingress/main.go /tmp/prufai-minio-ingress.go
RUN GOTOOLCHAIN=go1.26.10 go build -trimpath -o /go/bin/prufai-minio-ingress /tmp/prufai-minio-ingress.go
FROM ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
RUN groupadd --gid 10001 minio && useradd --uid 10001 --gid 10001 minio \
    && mkdir /data && chown 10001:10001 /data
COPY --from=build /go/bin/minio /usr/local/bin/minio
COPY --from=build /go/bin/prufai-minio-ingress /usr/local/bin/prufai-minio-ingress
LABEL org.prufai.storage-ingress="guarded-v1"
USER 10001:10001
EXPOSE 9000 9001
ENTRYPOINT ["/usr/local/bin/prufai-minio-ingress"]
