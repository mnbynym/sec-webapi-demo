"""5-3 オブザーバビリティ：OpenTelemetry による計装

アプリはベンダー非依存の OTel SDK だけで計装し、OTLP で送信する。
送信先（ローカルの grafana/otel-lgtm、Datadog など）は OTEL_EXPORTER_OTLP_ENDPOINT で切り替える。
"""

import json
import logging

from fastapi import FastAPI
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.logging import LoggingInstrumentor
from opentelemetry.instrumentation.pymongo import PymongoInstrumentor
from opentelemetry.instrumentation.redis import RedisInstrumentor
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

from app.config import Settings


class JsonFormatter(logging.Formatter):
    """構造化ログ（JSON）。trace_id / span_id を埋め込み、トレースとログを相関させる"""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "trace_id": getattr(record, "otelTraceID", None),
            "span_id": getattr(record, "otelSpanID", None),
        }
        if record.exc_info:
            entry["exc"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)


def setup_telemetry(app: FastAPI, s: Settings) -> None:
    resource = Resource.create({"service.name": s.otel_service_name, "deployment.environment": s.app_mode})
    base = s.otel_exporter_otlp_endpoint.rstrip("/")

    tp = TracerProvider(resource=resource)
    tp.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{base}/v1/traces")))
    trace.set_tracer_provider(tp)

    reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{base}/v1/metrics"), export_interval_millis=10_000
    )
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=[reader]))

    lp = LoggerProvider(resource=resource)
    lp.add_log_record_processor(BatchLogRecordProcessor(OTLPLogExporter(endpoint=f"{base}/v1/logs")))
    set_logger_provider(lp)
    logging.getLogger().addHandler(LoggingHandler(level=logging.INFO, logger_provider=lp))

    LoggingInstrumentor().instrument(set_logging_format=False)  # LogRecord に otelTraceID 等を注入
    AsyncPGInstrumentor().instrument()
    PymongoInstrumentor().instrument()
    RedisInstrumentor().instrument()
    HTTPXClientInstrumentor().instrument()  # W3C traceparent を下流へ伝播
    FastAPIInstrumentor.instrument_app(app, excluded_urls="healthz,readyz")
