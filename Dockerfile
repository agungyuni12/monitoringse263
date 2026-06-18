# Stage 1: Build
FROM golang:alpine AS builder

WORKDIR /app

COPY go.mod go.sum ./
COPY vendor ./vendor

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -mod=vendor -o monitoringse .

# Stage 2: Run
FROM alpine:latest

WORKDIR /app

COPY --from=builder /app/monitoringse .
COPY --from=builder /app/templates ./templates
COPY --from=builder /app/static ./static
COPY --from=builder /app/geo ./geo

EXPOSE 8080

CMD ["./monitoringse"]
