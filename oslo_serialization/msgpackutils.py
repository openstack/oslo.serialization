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
Msgpack related utilities.

This module provides a few things:

#. A handy registry for getting an object down to something that can be
   msgpack serialized.  See :class:`.HandlerRegistry`.
#. Wrappers around :func:`.loads` and :func:`.dumps`. The :func:`.dumps`
   wrapper will automatically use
   the :py:attr:`~oslo_serialization.msgpackutils.default_registry` for
   you if needed.
'''


import datetime
import functools
import itertools
import sys
import uuid

import msgpack
from oslo_utils import importutils
from pytz import timezone
import six
import six.moves.xmlrpc_client as xmlrpclib

netaddr = importutils.try_import("netaddr")

# NOTE(harlowja): itertools.count only started to take a step value
# in python 2.7+ so we can't use it in 2.6...
if sys.version_info[0:2] == (2, 6):
    _PY26 = True
else:
    _PY26 = False

# Expose these so that users don't have to import msgpack to gain these.

PackException = msgpack.PackException
UnpackException = msgpack.UnpackException


class HandlerRegistry(object):
    """Registry of *type* specific msgpack handlers extensions.

    See: https://github.com/msgpack/msgpack/blob/master/spec.md#formats-ext

    Do note that due to the current limitations in the msgpack python
    library we can not *currently* dump/load a tuple without converting
    it to a list.

    This may be fixed in: https://github.com/msgpack/msgpack-python/pull/100
    """

    # Applications can assign 0 to 127 to store
    # application-specific type information...
    min_value = 0
    max_value = 127

    def __init__(self):
        self._handlers = {}
        self.frozen = False

    def __iter__(self):
        return six.itervalues(self._handlers)

    def register(self, handler):
        """Register a extension handler to handle its associated type."""
        if self.frozen:
            raise ValueError("Frozen handler registry can't be modified")
        ident = handler.identity
        if ident < self.min_value:
            raise ValueError("Handler '%s' identity must be greater"
                             " or equal to %s" % (handler, self.min_value))
        if ident > self.max_value:
            raise ValueError("Handler '%s' identity must be less than"
                             " or equal to %s" % (handler, self.max_value))
        if ident in self._handlers:
            raise ValueError("Already registered handler with"
                             " identity %s: %s" % (ident,
                                                   self._handlers[ident]))
        else:
            self._handlers[ident] = handler

    def __len__(self):
        return len(self._handlers)

    def get(self, identity):
        """Get the handle for the given numeric identity (or none)."""
        return self._handlers.get(identity, None)

    def match(self, obj):
        """Match the registries handlers to the given object (or none)."""
        for handler in six.itervalues(self._handlers):
            if isinstance(obj, handler.handles):
                return handler
        return None


class UUIDHandler(object):
    identity = 0
    handles = (uuid.UUID,)

    @staticmethod
    def serialize(obj):
        return six.text_type(obj.hex).encode('ascii')

    @staticmethod
    def deserialize(data):
        return uuid.UUID(hex=six.text_type(data, encoding='ascii'))


class DateTimeHandler(object):
    identity = 1
    handles = (datetime.datetime,)

    def __init__(self, registry):
        self._registry = registry

    def serialize(self, dt):
        dct = {
            'day': dt.day,
            'month': dt.month,
            'year': dt.year,
            'hour': dt.hour,
            'minute': dt.minute,
            'second': dt.second,
            'microsecond': dt.microsecond,
        }
        if dt.tzinfo:
            dct['tz'] = dt.tzinfo.tzname(None)
        return dumps(dct, registry=self._registry)

    def deserialize(self, blob):
        dct = loads(blob, registry=self._registry)
        dt = datetime.datetime(day=dct['day'],
                               month=dct['month'],
                               year=dct['year'],
                               hour=dct['hour'],
                               minute=dct['minute'],
                               second=dct['second'],
                               microsecond=dct['microsecond'])
        if 'tz' in dct:
            tzinfo = timezone(dct['tz'])
            dt = tzinfo.localize(dt)
        return dt


class CountHandler(object):
    identity = 2
    handles = (itertools.count,)

    @staticmethod
    def serialize(obj):
        # FIXME(harlowja): figure out a better way to avoid hacking into
        # the string representation of count to get at the right numbers...
        obj = six.text_type(obj)
        start = obj.find("(") + 1
        end = obj.rfind(")")
        pieces = obj[start:end].split(",")
        if len(pieces) == 1:
            start = int(pieces[0])
            step = 1
        else:
            start = int(pieces[0])
            step = int(pieces[1])
        return msgpack.packb([start, step])

    @staticmethod
    def deserialize(data):
        value = msgpack.unpackb(data)
        start, step = value
        if not _PY26:
            return itertools.count(start, step)
        else:
            if step != 1:
                raise ValueError("Python 2.6.x does not support steps"
                                 " that are not equal to one")
            return itertools.count(start)


if netaddr is not None:
    class NetAddrIPHandler(object):
        identity = 3
        handles = (netaddr.IPAddress,)

        @staticmethod
        def serialize(obj):
            return msgpack.packb(obj.value)

        @staticmethod
        def deserialize(data):
            return netaddr.IPAddress(msgpack.unpackb(data))
else:
    NetAddrIPHandler = None


class SetHandler(object):
    identity = 4
    handles = (set,)

    def __init__(self, registry):
        self._registry = registry

    def serialize(self, obj):
        return dumps(list(obj), registry=self._registry)

    def deserialize(self, data):
        return self.handles[0](loads(data, registry=self._registry))


class FrozenSetHandler(SetHandler):
    identity = 5
    handles = (frozenset,)


class XMLRPCDateTimeHandler(object):
    handles = (xmlrpclib.DateTime,)
    identity = 6

    def __init__(self, registry):
        self._handler = DateTimeHandler(registry)

    def serialize(self, obj):
        dt = datetime.datetime(*tuple(obj.timetuple())[:6])
        return self._handler.serialize(dt)

    def deserialize(self, blob):
        dt = self._handler.deserialize(blob)
        return xmlrpclib.DateTime(dt.timetuple())


class DateHandler(object):
    identity = 7
    handles = (datetime.date,)

    def __init__(self, registry):
        self._registry = registry

    def serialize(self, d):
        dct = {
            'year': d.year,
            'month': d.month,
            'day': d.day,
        }
        return dumps(dct, registry=self._registry)

    def deserialize(self, blob):
        dct = loads(blob, registry=self._registry)
        return datetime.date(year=dct['year'],
                             month=dct['month'],
                             day=dct['day'])


def _serializer(registry, obj):
    handler = registry.match(obj)
    if handler is None:
        raise TypeError("No serialization handler registered"
                        " for type '%s'" % (type(obj).__name__))
    return msgpack.ExtType(handler.identity, handler.serialize(obj))


def _unserializer(registry, code, data):
    handler = registry.get(code)
    if handler is None:
        return msgpack.ExtType(code, data)
    else:
        return handler.deserialize(data)


def _create_default_registry():
    registry = HandlerRegistry()
    registry.register(DateTimeHandler(registry))
    registry.register(DateHandler(registry))
    registry.register(UUIDHandler())
    registry.register(CountHandler())
    registry.register(SetHandler(registry))
    registry.register(FrozenSetHandler(registry))
    if netaddr is not None:
        registry.register(NetAddrIPHandler())
    registry.register(XMLRPCDateTimeHandler(registry))
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
"""


def load(fp, registry=None):
    """Deserialize ``fp`` into a Python object."""
    if registry is None:
        registry = default_registry
    # NOTE(harlowja): the reason we can't use the more native msgpack functions
    # here is that the unpack() function (oddly) doesn't seem to take a
    # 'ext_hook' parameter..
    ext_hook = functools.partial(_unserializer, registry)
    return msgpack.Unpacker(fp, ext_hook=ext_hook, encoding='utf-8').unpack()


def dump(obj, fp, registry=None):
    """Serialize ``obj`` as a messagepack formatted stream to ``fp``."""
    if registry is None:
        registry = default_registry
    return msgpack.pack(obj, fp,
                        default=functools.partial(_serializer, registry),
                        use_bin_type=True)


def dumps(obj, registry=None):
    """Serialize ``obj`` to a messagepack formatted ``str``."""
    if registry is None:
        registry = default_registry
    return msgpack.packb(obj,
                         default=functools.partial(_serializer, registry),
                         use_bin_type=True)


def loads(s, registry=None):
    """Deserialize ``s`` messagepack ``str`` into a Python object."""
    if registry is None:
        registry = default_registry
    ext_hook = functools.partial(_unserializer, registry)
    return msgpack.unpackb(s, ext_hook=ext_hook, encoding='utf-8')
