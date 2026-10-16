import datetime

from google.protobuf import timestamp_pb2 as _timestamp_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Warehouse(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    WAREHOUSE_UNSPECIFIED: _ClassVar[Warehouse]
    WAREHOUSE_TOKYO: _ClassVar[Warehouse]
    WAREHOUSE_OSAKA: _ClassVar[Warehouse]
    WAREHOUSE_FUKUOKA: _ClassVar[Warehouse]
WAREHOUSE_UNSPECIFIED: Warehouse
WAREHOUSE_TOKYO: Warehouse
WAREHOUSE_OSAKA: Warehouse
WAREHOUSE_FUKUOKA: Warehouse

class GetStockRequest(_message.Message):
    __slots__ = ("sku",)
    SKU_FIELD_NUMBER: _ClassVar[int]
    sku: str
    def __init__(self, sku: _Optional[str] = ...) -> None: ...

class Stock(_message.Message):
    __slots__ = ("sku", "available", "warehouse", "updated_at")
    SKU_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_FIELD_NUMBER: _ClassVar[int]
    WAREHOUSE_FIELD_NUMBER: _ClassVar[int]
    UPDATED_AT_FIELD_NUMBER: _ClassVar[int]
    sku: str
    available: int
    warehouse: Warehouse
    updated_at: _timestamp_pb2.Timestamp
    def __init__(self, sku: _Optional[str] = ..., available: _Optional[int] = ..., warehouse: _Optional[_Union[Warehouse, str]] = ..., updated_at: _Optional[_Union[datetime.datetime, _timestamp_pb2.Timestamp, _Mapping]] = ...) -> None: ...

class BatchGetStockRequest(_message.Message):
    __slots__ = ("skus",)
    SKUS_FIELD_NUMBER: _ClassVar[int]
    skus: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, skus: _Optional[_Iterable[str]] = ...) -> None: ...

class BatchGetStockResponse(_message.Message):
    __slots__ = ("stocks",)
    STOCKS_FIELD_NUMBER: _ClassVar[int]
    stocks: _containers.RepeatedCompositeFieldContainer[Stock]
    def __init__(self, stocks: _Optional[_Iterable[_Union[Stock, _Mapping]]] = ...) -> None: ...

class WatchStockRequest(_message.Message):
    __slots__ = ("sku", "max_events")
    SKU_FIELD_NUMBER: _ClassVar[int]
    MAX_EVENTS_FIELD_NUMBER: _ClassVar[int]
    sku: str
    max_events: int
    def __init__(self, sku: _Optional[str] = ..., max_events: _Optional[int] = ...) -> None: ...

class StockEvent(_message.Message):
    __slots__ = ("stock", "delta")
    STOCK_FIELD_NUMBER: _ClassVar[int]
    DELTA_FIELD_NUMBER: _ClassVar[int]
    stock: Stock
    delta: int
    def __init__(self, stock: _Optional[_Union[Stock, _Mapping]] = ..., delta: _Optional[int] = ...) -> None: ...
