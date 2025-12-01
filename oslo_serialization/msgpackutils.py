#    Copyright (C) 2015 Yahoo! Inc. All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

'''
MessagePack related utilities.

This module provides a few things:

#. A handy registry for getting an object down to something that can be
   msgpack serialized.  See :class:`.HandlerRegistry`.
#. Wrappers around :func:`.loads` and :func:`.dumps`. The :func:`.dumps`
   wrapper will automatically use
   the :py:attr:`~oslo_serialization.msgpackutils.default_registry` for
   you if needed.

.. versionadded:: 1.3
'''

from collections.abc import Iterator
import datetime
import functools
import itertools
from typing import Any, Protocol, TypeVar, TYPE_CHECKING
import uuid
from xmlrpc import client as xmlrpclib
import zoneinfo

import msgpack  # type: ignore

from oslo_serialization._types import ReadableStream, SupportsWrite

try:
    import netaddr
except ImportError:
    netaddr = None  # type: ignore

if TYPE_CHECKING:
    from typing_extensions import Self

    # this is not subscriptable until Python 3.11
    ItertoolsCountT = itertools.count[Any]
else:
    ItertoolsCountT = itertools.count


class Interval:
    """Small and/or simple immutable interval class.

    Interval checking is **inclusive** of the min/max boundaries.
    """

    def __init__(self, min_value: int, max_value: int) -> None:
        if min_value > max_value:
            raise ValueError(
                f"Minimum value {min_value} must be less than"
                f" or equal to maximum value {max_value}"
            )
        self._min_value = min_value
        self._max_value = max_value

    @property
    def min_value(self) -> int:
        return self._min_value

    @property
    def max_value(self) -> int:
        return self._max_value

    def __contains__(self, value: int) -> bool:
        return value >= self.min_value and value <= self.max_value

    def __repr__(self) -> str:
        return f'Interval({self._min_value}, {self._max_value})'


# Expose these so that users don't have to import msgpack to gain these.

PackException = msgpack.PackException
UnpackException = msgpack.UnpackException


_T = TypeVar('_T')


class HandlerProtocol(Protocol[_T]):
    identity: int
    handles: tuple[type[_T]]

    def serialize(self, obj: _T) -> bytes: ...

    def deserialize(self, data: bytes) -> _T: ...


class HandlerRegistry:
    """Registry of *type* specific msgpack handlers extensions.

    See: https://github.com/msgpack/msgpack/blob/master/spec.md#formats-ext

    Do note that due to the current limitations in the msgpack python
    library we can not *currently* dump/load a tuple without converting
    it to a list.

    This may be fixed in: https://github.com/msgpack/msgpack-python/pull/100

    .. versionadded:: 1.5
    """

    reserved_extension_range = Interval(0, 32)
    """
    These ranges are **always** reserved for use by ``oslo.serialization`` and
    its own add-ons extensions (these extensions are meant to be generally
    applicable to all of python).
    """

    non_reserved_extension_range = Interval(33, 127)
    """
    These ranges are **always** reserved for use by applications building
    their own type specific handlers (the meaning of extensions in this range
    will typically vary depending on application).
    """

    min_value = 0
    """
    Applications can assign 0 to 127 to store application (or library)
    specific type handlers; see above ranges for what is reserved by this
    library and what is not.
    """

    max_value = 127
    """
    Applications can assign 0 to 127 to store application (or library)
    specific type handlers; see above ranges for what is reserved by this
    library and what is not.
    """

    def __init__(self) -> None:
        self._handlers: dict[int, list[HandlerProtocol[Any]]] = {}
        self._num_handlers = 0
        self.frozen = False

    def __iter__(self) -> Iterator[Any]:
        """Iterates over **all** registered handlers."""
        for handlers in self._handlers.values():
            yield from handlers

    def register(
        self,
        handler: HandlerProtocol[Any],
        reserved: bool = False,
        override: bool = False,
    ) -> None:
        """Register a extension handler to handle its associated type."""
        if self.frozen:
            raise ValueError("Frozen handler registry can't be modified")

        if reserved:
            ok_interval = self.reserved_extension_range
        else:
            ok_interval = self.non_reserved_extension_range

        ident = handler.identity
        if ident < ok_interval.min_value:
            raise ValueError(
                f"Handler '{handler}' identity must be greater"
                f" or equal to {ok_interval.min_value}"
            )

        if ident > ok_interval.max_value:
            raise ValueError(
                f"Handler '{handler}' identity must be less than"
                f" or equal to {ok_interval.max_value}"
            )

        if ident in self._handlers and override:
            existing_handlers = self._handlers[ident]
            # Insert at the front so that overrides get selected before
            # whatever existed before the override...
            existing_handlers.insert(0, handler)
            self._num_handlers += 1
        elif ident in self._handlers and not override:
            raise ValueError(
                "Already registered handler(s) with"
                f" identity {ident}: {self._handlers[ident]}"
            )
        else:
            self._handlers[ident] = [handler]
            self._num_handlers += 1

    def __len__(self) -> int:
        """Return how many extension handlers are registered."""
        return self._num_handlers

    def __contains__(self, identity: int) -> bool:
        """Return if any handler exists for the given identity (number)."""
        return identity in self._handlers

    def copy(self, unfreeze: bool = False) -> 'Self':
        """Deep copy the given registry (and its handlers)."""
        c = type(self)()
        for ident, handlers in self._handlers.items():
            cloned_handlers = []
            for h in handlers:
                if hasattr(h, 'copy'):
                    h = h.copy(c)
                cloned_handlers.append(h)
            c._handlers[ident] = cloned_handlers
            c._num_handlers += len(cloned_handlers)
        if not unfreeze and self.frozen:
            c.frozen = True
        return c

    def get(self, identity: int) -> HandlerProtocol[Any] | None:
        """Get the handler for the given numeric identity (or none)."""
        maybe_handlers = self._handlers.get(identity)
        if maybe_handlers:
            # Prefer the first (if there are many) as this is how we
            # override built-in extensions (for those that wish to do this).
            return maybe_handlers[0]
        else:
            return None

    def match(self, obj: Any) -> HandlerProtocol[Any] | None:
        """Match the registries handlers to the given object (or none)."""
        for possible_handlers in self._handlers.values():
            for h in possible_handlers:
                if isinstance(obj, h.handles):
                    return h
        return None


class UUIDHandler(HandlerProtocol[uuid.UUID]):
    identity = 0
    handles = (uuid.UUID,)

    def serialize(self, obj: uuid.UUID) -> bytes:
        return str(obj.hex).encode('ascii')

    def deserialize(self, data: bytes) -> uuid.UUID:
        return uuid.UUID(hex=str(data, encoding='ascii'))


class DateTimeHandler(HandlerProtocol[datetime.datetime]):
    identity = 1
    handles = (datetime.datetime,)

    def __init__(self, registry: HandlerRegistry):
        self._registry = registry

    def copy(self, registry: HandlerRegistry) -> 'Self':
        return type(self)(registry)

    def serialize(self, dt: datetime.datetime) -> bytes:
        dct: dict[str, Any] = {
            'day': dt.day,
            'month': dt.month,
            'year': dt.year,
            'hour': dt.hour,
            'minute': dt.minute,
            'second': dt.second,
            'microsecond': dt.microsecond,
        }
        if dt.tzinfo:
            dct['tz'] = str(dt.tzinfo)
        return dumps(dct, registry=self._registry)

    def deserialize(self, blob: bytes) -> datetime.datetime:
        dct = loads(blob, registry=self._registry)

        if b'day' in dct:
            # NOTE(sileht): oslo.serialization <= 2.4.1 was
            # storing thing as unicode for py3 while is was
            # bytes for py2
            # For python2, we don't care bytes or unicode works
            # for dict keys and tz
            # But for python3, we have some backward compability
            # to take care in case of the payload have been produced
            # by python2 and now read by python3
            dct = {k.decode("ascii"): v for k, v in dct.items()}
            if 'tz' in dct:
                dct['tz'] = dct['tz'].decode("ascii")

        dt = datetime.datetime(
            day=dct['day'],
            month=dct['month'],
            year=dct['year'],
            hour=dct['hour'],
            minute=dct['minute'],
            second=dct['second'],
            microsecond=dct['microsecond'],
        )
        if 'tz' in dct and dct['tz']:
            dt = dt.replace(tzinfo=zoneinfo.ZoneInfo(dct['tz']))
        return dt


class CountHandler(HandlerProtocol[ItertoolsCountT]):
    identity = 2
    handles = (itertools.count,)

    def serialize(self, obj: ItertoolsCountT) -> bytes:
        # FIXME(harlowja): figure out a better way to avoid hacking into
        # the string representation of count to get at the right numbers...
        data = str(obj)
        start = data.find("(") + 1
        end = data.rfind(")")
        pieces = data[start:end].split(",")
        if len(pieces) == 1:
            start = int(pieces[0])
            step = 1
        else:
            start = int(pieces[0])
            step = int(pieces[1])
        return msgpack.packb([start, step])  # type: ignore

    def deserialize(self, data: bytes) -> ItertoolsCountT:
        value = msgpack.unpackb(data)
        start, step = value
        return itertools.count(start, step)


if netaddr is not None:

    class NetAddrIPHandler(HandlerProtocol[netaddr.IPAddress]):
        identity = 3
        handles = (netaddr.IPAddress,)

        def serialize(self, obj: netaddr.IPAddress) -> bytes:
            return msgpack.packb(obj.value)  # type: ignore

        def deserialize(self, data: bytes) -> netaddr.IPAddress:
            return netaddr.IPAddress(msgpack.unpackb(data))
else:
    NetAddrIPHandler = None  # type: ignore


class SetHandler(HandlerProtocol[set[Any]]):
    identity = 4
    handles = (set,)

    def __init__(self, registry: HandlerRegistry) -> None:
        self._registry = registry

    def copy(self, registry: HandlerRegistry) -> 'Self':
        return type(self)(registry)

    def serialize(self, obj: set[Any]) -> bytes:
        return dumps(list(obj), registry=self._registry)

    def deserialize(self, data: bytes) -> set[Any]:
        return self.handles[0](loads(data, registry=self._registry))


class FrozenSetHandler(HandlerProtocol[frozenset[Any]]):
    identity = 5
    handles = (frozenset,)

    def __init__(self, registry: HandlerRegistry) -> None:
        self._registry = registry

    def copy(self, registry: HandlerRegistry) -> 'Self':
        return type(self)(registry)

    def serialize(self, obj: frozenset[Any]) -> bytes:
        return dumps(list(obj), registry=self._registry)

    def deserialize(self, data: bytes) -> frozenset[Any]:
        return frozenset(loads(data, registry=self._registry))


class XMLRPCDateTimeHandler(HandlerProtocol[xmlrpclib.DateTime]):
    handles = (xmlrpclib.DateTime,)
    identity = 6

    def __init__(self, registry: HandlerRegistry) -> None:
        self._handler = DateTimeHandler(registry)

    def copy(self, registry: HandlerRegistry) -> 'Self':
        return type(self)(registry)

    def serialize(self, obj: xmlrpclib.DateTime) -> bytes:
        dt = datetime.datetime(*tuple(obj.timetuple())[:6])  # type: ignore
        return self._handler.serialize(dt)

    def deserialize(self, data: bytes) -> xmlrpclib.DateTime:
        dt = self._handler.deserialize(data)
        return xmlrpclib.DateTime(dt.timetuple())


class DateHandler(HandlerProtocol[datetime.date]):
    identity = 7
    handles = (datetime.date,)

    def __init__(self, registry: HandlerRegistry) -> None:
        self._registry = registry

    def copy(self, registry: HandlerRegistry) -> 'Self':
        return type(self)(registry)

    def serialize(self, obj: datetime.date) -> bytes:
        dct = {
            'year': obj.year,
            'month': obj.month,
            'day': obj.day,
        }
        return dumps(dct, registry=self._registry)

    def deserialize(self, blob: bytes) -> datetime.date:
        dct = loads(blob, registry=self._registry)
        if b'day' in dct:
            # NOTE(sileht): see DateTimeHandler.deserialize()
            dct = {k.decode("ascii"): v for k, v in dct.items()}

        return datetime.date(
            year=dct['year'], month=dct['month'], day=dct['day']
        )


def _serializer(registry: HandlerRegistry, obj: Any) -> bytes:
    handler = registry.match(obj)
    if handler is None:
        raise ValueError(
            "No serialization handler registered"
            f" for type '{type(obj).__name__}'"
        )
    return msgpack.ExtType(handler.identity, handler.serialize(obj))  # type: ignore


def _unserializer(registry: HandlerRegistry, code: int, data: bytes) -> Any:
    handler = registry.get(code)
    if not handler:
        return msgpack.ExtType(code, data)
    else:
        return handler.deserialize(data)


def _create_default_registry() -> HandlerRegistry:
    registry = HandlerRegistry()
    registry.register(DateTimeHandler(registry), reserved=True)
    registry.register(DateHandler(registry), reserved=True)
    registry.register(UUIDHandler(), reserved=True)
    registry.register(CountHandler(), reserved=True)
    registry.register(SetHandler(registry), reserved=True)
    registry.register(FrozenSetHandler(registry), reserved=True)
    if netaddr is not None:
        registry.register(NetAddrIPHandler(), reserved=True)
    registry.register(XMLRPCDateTimeHandler(registry), reserved=True)
    registry.frozen = True
    return registry


default_registry = _create_default_registry()
"""
Default, read-only/frozen registry that will be used when none is provided.

This registry has msgpack extensions for the following:

* ``DateTime`` objects.
* ``Date`` objects.
* ``UUID`` objects.
* ``itertools.count`` objects/iterators.
* ``set`` and ``frozenset`` container(s).
* ``netaddr.IPAddress`` objects (only if ``netaddr`` is importable).
* ``xmlrpclib.DateTime`` datetime objects.

.. versionadded:: 1.5
"""


def load(fp: ReadableStream, registry: HandlerRegistry | None = None) -> Any:
    """Deserialize ``fp`` into a Python object.

    .. versionchanged:: 1.5
       Added *registry* parameter.
    """
    if registry is None:
        registry = default_registry
    # NOTE(harlowja): the reason we can't use the more native msgpack functions
    # here is that the unpack() function (oddly) doesn't seem to take a
    # 'ext_hook' parameter..
    ext_hook = functools.partial(_unserializer, registry)
    return msgpack.Unpacker(fp, ext_hook=ext_hook, raw=False).unpack()


def dump(
    obj: Any, fp: SupportsWrite, registry: HandlerRegistry | None = None
) -> None:
    """Serialize ``obj`` as a messagepack formatted stream to ``fp``.

    .. versionchanged:: 1.5
       Added *registry* parameter.
    """
    if registry is None:
        registry = default_registry
    return msgpack.pack(  # type: ignore
        obj,
        fp,
        default=functools.partial(_serializer, registry),
        use_bin_type=True,
    )


def dumps(obj: Any, registry: HandlerRegistry | None = None) -> bytes:
    """Serialize ``obj`` to a messagepack formatted ``str``.

    .. versionchanged:: 1.5
       Added *registry* parameter.
    """
    if registry is None:
        registry = default_registry
    return msgpack.packb(  # type: ignore
        obj,
        default=functools.partial(_serializer, registry),
        use_bin_type=True,
    )


def loads(s: bytes, registry: HandlerRegistry | None = None) -> Any:
    """Deserialize ``s`` messagepack ``str`` into a Python object.

    .. versionchanged:: 1.5
       Added *registry* parameter.
    """
    if registry is None:
        registry = default_registry
    ext_hook = functools.partial(_unserializer, registry)
    return msgpack.unpackb(s, ext_hook=ext_hook, raw=False)
