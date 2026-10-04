# The telemetry backend as a container, for deploy/prod. Static binary on a
# distroless base: nothing in the image but the backend.
FROM docker.io/library/golang:1.25 AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/backend .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/backend /backend
USER nonroot
ENTRYPOINT ["/backend"]
