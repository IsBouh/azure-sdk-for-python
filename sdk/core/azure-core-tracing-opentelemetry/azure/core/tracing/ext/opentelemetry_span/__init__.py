# ------------------------------------
# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
# ------------------------------------
"""Implements azure.core.tracing.AbstractSpan to wrap OpenTelemetry spans."""

from opentelemetry.trace import Span, Tracer, SpanKind as OpenTelemetrySpanKind, tracer
from opentelemetry.context import Context
from opentelemetry.propagators import extract, inject

from azure.core.tracing import SpanKind  # pylint: disable=no-name-in-module

try:
    from typing import TYPE_CHECKING
except ImportError:
    TYPE_CHECKING = False

if TYPE_CHECKING:
    from typing import Dict, Optional, Union, Callable

    from azure.core.pipeline.transport import HttpRequest, HttpResponse

__version__ = "1.0.0b4"


def _get_headers_from_http_request_headers(headers: "Mapping[str, Any]", key: str):
    """Return headers that matches this key.

    Must comply to opentelemetry.context.propagation.httptextformat.Getter:
    Getter = typing.Callable[[_T, str], typing.List[str]]
    """
    return [headers[key]]


def _set_headers_from_http_request_headers(headers: "Mapping[str, Any]", key: str, value: str):
    """Set headers in the given headers dict.

    Must comply to opentelemetry.context.propagation.httptextformat.Setter:
    Setter = typing.Callable[[_T, str, str], None]
    """
    headers[key] = value


class OpenTelemetrySpan(object):
    """Wraps a given OpenTelemetry Span so that it implements azure.core.tracing.AbstractSpan"""

    def __init__(self, span=None, name="span"):
        # type: (Optional[Span], Optional[str]) -> None
        """
        If a span is not passed in, creates a new tracer. If the instrumentation key for Azure Exporter is given, will
        configure the azure exporter else will just create a new tracer.

        :param span: The OpenTelemetry span to wrap
        :type span: :class: OpenTelemetry.trace.Span
        :param name: The name of the OpenTelemetry span to create if a new span is needed
        :type name: str
        """
        tracer = self.get_current_tracer()
        self._span_instance = span or tracer.create_span(name=name)
        self._span_component = "component"
        self._http_user_agent = "http.user_agent"
        self._http_method = "http.method"
        self._http_url = "http.url"
        self._http_status_code = "http.status_code"
        self._current_ctxt_manager = None

    @property
    def span_instance(self):
        # type: () -> Span
        """
        :return: The OpenTelemetry span that is being wrapped.
        """
        return self._span_instance

    def span(self, name="span"):
        # type: (Optional[str]) -> OpenCensusSpan
        """
        Create a child span for the current span and append it to the child spans list in the span instance.
        :param name: Name of the child span
        :type name: str
        :return: The OpenCensusSpan that is wrapping the child span instance
        """
        return self.__class__(name=name)

    @property
    def kind(self):
        # type: () -> Optional[SpanKind]
        """Get the span kind of this span."""
        value = self.span_instance.kind
        return (
            SpanKind.CLIENT if value == OpenCensusSpanKind.CLIENT else
            SpanKind.PRODUCER if value == OpenCensusSpanKind.PRODUCER else
            SpanKind.SERVER if value == OpenCensusSpanKind.SERVER else
            SpanKind.CONSUMER if value == OpenCensusSpanKind.CONSUMER else
            SpanKind.INTERNAL if value == OpenCensusSpanKind.INTERNAL else
            SpanKind.UNSPECIFIED if value == OpenCensusSpanKind.UNSPECIFIED else
            None
        )


    @kind.setter
    def kind(self, value):
        # type: (SpanKind) -> None
        """Set the span kind of this span."""
        kind = (
            OpenTelemetrySpanKind.CLIENT if value == SpanKind.CLIENT else
            OpenTelemetrySpanKind.PRODUCER if value == SpanKind.PRODUCER else
            OpenTelemetrySpanKind.SERVER if value == SpanKind.SERVER else
            OpenTelemetrySpanKind.CONSUMER if value == SpanKind.CONSUMER else
            OpenTelemetrySpanKind.INTERNAL if value == SpanKind.INTERNAL else
            OpenTelemetrySpanKind.UNSPECIFIED if value == SpanKind.UNSPECIFIED else
            None
        )
        if kind is None:
            raise ValueError("Kind {} is not supported in OpenTelemetry".format(value))
        self.span_instance.kind = kind

    def __enter__(self):
        """Start a span."""
        self._span_instance.start()
        self._current_ctxt_manager = self.get_current_tracer().use_span(self._span_instance, end_on_exit=True)
        self._current_ctxt_manager.__enter__()
        return self

    def __exit__(self, exception_type, exception_value, traceback):
        """Finish a span."""
        if not self._current_ctxt_manager:
            raise ValueError("Trying to manually exit a ctxt manager that didn't started")
        self._current_ctxt_manager.__exit__(exception_type, exception_value, traceback)

    def start(self):
        # type: () -> None
        """Set the start time for a span."""
        self.span_instance.start()

    def finish(self):
        # type: () -> None
        """Set the end time for a span."""
        self.span_instance.end()

    def to_header(self):
        # type: () -> Dict[str, str]
        """
        Returns a dictionary with the header labels and values.
        :return: A key value pair dictionary
        """
        temp_headers = {} # type: Dict[str, str]
        inject(self.get_current_tracer(), _set_headers_from_http_request_headers, temp_headers)
        return temp_headers

    def add_attribute(self, key, value):
        # type: (str, Union[str, int]) -> None
        """
        Add attribute (key value pair) to the current span.

        :param key: The key of the key value pair
        :type key: str
        :param value: The value of the key value pair
        :type value: str
        """
        self.span_instance.set_attribute(key, value)

    def set_http_attributes(self, request, response=None):
        # type: (HttpRequest, Optional[HttpResponse]) -> None
        """
        Add correct attributes for a http client span.

        :param request: The request made
        :type request: HttpRequest
        :param response: The response received by the server. Is None if no response received.
        :type response: HttpResponse
        """
        self.kind = SpanKind.CLIENT
        self.add_attribute(self._span_component, "http")
        self.add_attribute(self._http_method, request.method)
        self.add_attribute(self._http_url, request.url)
        user_agent = request.headers.get("User-Agent")
        if user_agent:
            self.add_attribute(self._http_user_agent, user_agent)
        if response:
            self.add_attribute(self._http_status_code, response.status_code)
        else:
            self.add_attribute(self._http_status_code, 504)

    def get_trace_parent(self):
        """Return traceparent string as defined in W3C trace context specification.

        Example:
        Value = 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
        base16(version) = 00
        base16(trace-id) = 4bf92f3577b34da6a3ce929d0e0e4736
        base16(parent-id) = 00f067aa0ba902b7
        base16(trace-flags) = 01  // sampled

        :return: a traceparent string
        :rtype: str
        """
        return self.to_header()['traceparent']

    @classmethod
    def link(cls, traceparent):
        # type: (str) -> None
        """
        Links the context to the current tracer.

        :param traceparent: A complete traceparent
        :type traceparent: str
        """
        cls.link_from_headers({
            'traceparent': traceparent
        })

    @classmethod
    def link_from_headers(cls, headers):
        # type: (Dict[str, str]) -> None
        """
        Given a dictionary, extracts the context and links the context to the current tracer.

        :param headers: A key value pair dictionary
        :type headers: dict
        """
        ctx = extract(_get_headers_from_http_request_headers, headers)
        current_span = cls.get_current_span()
        current_span.add_link(ctx)

    @classmethod
    def get_current_span(cls):
        # type: () -> Span
        """
        Get the current span from the execution context. Return None otherwise.
        """
        return cls.get_current_tracer().get_current_span()

    @classmethod
    def get_current_tracer(cls):
        # type: () -> Tracer
        """
        Get the current tracer from the execution context. Return None otherwise.
        """
        return tracer()

    @classmethod
    def change_context(cls, span):
        # type: (Span) -> ContextManager
        """Change the context for the life of this context manager.
        """
        return cls.get_current_tracer().use_span(span, end_on_exit=False)

    @classmethod
    def set_current_span(cls, span):
        # type: (Span) -> None
        """Not supported by OpenTelemetry.
        """
        raise NotImplementedError("set_current_span is not supported by OpenTelemetry plugin. Use ChangeContext instead.")

    @classmethod
    def set_current_tracer(cls, tracer):
        # type: (Tracer) -> None
        """
        Set the given tracer as the current tracer in the execution context.
        :param tracer: The tracer to set the current tracer as
        :type tracer: :class: OpenTelemetry.trace.Tracer
        """
        # Do nothing, if you're able to get two tracer with OpenTelemetry that's a surprise!
        pass

    @classmethod
    def with_current_context(cls, func):
        # type: (Callable) -> Callable
        """Passes the current spans to the new context the function will be run in.

        :param func: The function that will be run in the new context
        :return: The target the pass in instead of the function
        """
        return Context.with_current_context(func)