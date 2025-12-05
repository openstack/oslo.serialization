# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# Copyright 2011 Justin Santa Barbara
# All Rights Reserved.
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
JSON related utilities.

This module provides a few things:

#. A handy function for getting an object down to something that can be
   JSON serialized. See :func:`.to_primitive`.
#. Wrappers around :func:`.loads` and :func:`.dumps`. The :func:`.dumps`
   wrapper will automatically use :func:`.to_primitive` for you if needed.
'''

import codecs
from collections.abc import Callable
import datetime
import functools
import inspect
import io
import itertools
import json
from typing import Any, TypeAlias
import uuid
from xmlrpc import client as xmlrpclib

from oslo_utils import encodeutils
from oslo_utils import importutils
from oslo_utils import timeutils

from oslo_serialization._types import ReadableStream, SupportsWrite

_ISO8601_DATE_FORMAT = '%Y-%m-%d'

ipaddress = importutils.try_import("ipaddress")
netaddr = importutils.try_import("netaddr")

_nasty_type_tests: list[Callable[..., bool]] = [
    inspect.ismodule,
    inspect.isclass,
    inspect.ismethod,
    inspect.isfunction,
    inspect.isgeneratorfunction,
    inspect.isgenerator,
    inspect.istraceback,
    inspect.isframe,
    inspect.iscode,
    inspect.isbuiltin,
    inspect.isroutine,
    inspect.isabstract,
]

_simple_types = (str, int, type(None), bool, float)

_SimpleTypes: TypeAlias = str | int | None | bool | float


# We need recursive types to be able to type this
def to_primitive(
    value: Any,
    convert_instances: bool = False,
    convert_datetime: bool = True,
    level: int = 0,
    max_depth: int = 3,
    encoding: str = 'utf-8',
    fallback: Callable[[Any], _SimpleTypes] | None = None,
) -> Any:
    """Convert a complex object into primitives.

    Handy for JSON serialization. We can optionally handle instances,
    but since this is a recursive function, we could have cyclical
    data structures.

    To handle cyclical data structures we could track the actual objects
    visited in a set, but not all objects are hashable. Instead we just
    track the depth of the object inspections and don't go too deep.

    Therefore, ``convert_instances=True`` is lossy ... be aware.

    If the object cannot be converted to primitive, it is returned unchanged
    if fallback is not set, return fallback(value) otherwise.

    .. versionchanged:: 2.22
       Added *fallback* parameter.

    .. versionchanged:: 1.3
       Support UUID encoding.

    .. versionchanged:: 1.6
       Dictionary keys are now also encoded.
    """
    orig_fallback = fallback
    if fallback is None:
        fallback = str

    # handle obvious types first - order of basic types determined by running
    # full tests on nova project, resulting in the following counts:
    # 572754 <type 'NoneType'>
    # 460353 <type 'int'>
    # 379632 <type 'unicode'>
    # 274610 <type 'str'>
    # 199918 <type 'dict'>
    # 114200 <type 'datetime.datetime'>
    #  51817 <type 'bool'>
    #  26164 <type 'list'>
    #   6491 <type 'float'>
    #    283 <type 'tuple'>
    #     19 <type 'long'>
    if isinstance(value, _simple_types):
        return value

    if isinstance(value, bytes):
        return value.decode(encoding=encoding)

    # It's not clear why xmlrpclib created their own DateTime type, but
    # for our purposes, make it a datetime type which is explicitly
    # handled
    if isinstance(value, xmlrpclib.DateTime):
        value = datetime.datetime(*tuple(value.timetuple())[:6])  # type: ignore

    if isinstance(value, datetime.datetime):
        if convert_datetime:
            return value.strftime(timeutils.PERFECT_TIME_FORMAT)
        else:
            return value

    if isinstance(value, datetime.date):
        if convert_datetime:
            return value.strftime(_ISO8601_DATE_FORMAT)
        else:
            return value

    if isinstance(value, uuid.UUID):
        return str(value)

    if netaddr and isinstance(value, (netaddr.IPAddress, netaddr.IPNetwork)):
        return str(value)

    if ipaddress and isinstance(
        value, (ipaddress.IPv4Address, ipaddress.IPv6Address)
    ):
        return str(value)

    # For exceptions, return the 'repr' of the exception object
    if isinstance(value, Exception):
        return repr(value)

    # value of itertools.count doesn't get caught by nasty_type_tests
    # and results in infinite loop when list(value) is called.
    if type(value) is itertools.count:
        return fallback(value)

    if any(test(value) for test in _nasty_type_tests):
        return fallback(value)

    if level > max_depth:
        return None

    # The try block may not be necessary after the class check above,
    # but just in case ...
    try:
        recursive = functools.partial(
            to_primitive,
            convert_instances=convert_instances,
            convert_datetime=convert_datetime,
            level=level,
            max_depth=max_depth,
            encoding=encoding,
            fallback=orig_fallback,
        )
        if isinstance(value, dict):
            return {recursive(k): recursive(v) for k, v in value.items()}
        elif hasattr(value, 'items'):
            return recursive(dict(value.items()), level=level + 1)
        elif hasattr(value, '__iter__') and not isinstance(value, io.IOBase):
            return list(map(recursive, value))
        elif convert_instances and hasattr(value, '__dict__'):
            # Likely an instance of something. Watch for cycles.
            # Ignore class member vars.
            return recursive(value.__dict__, level=level + 1)
    except TypeError:
        # Class objects are tricky since they may define something like
        # __iter__ defined but it isn't callable as list().
        return fallback(value)

    if orig_fallback is None:
        raise ValueError(f"Cannot convert {value!r} to primitive")

    return orig_fallback(value)


JSONEncoder = json.JSONEncoder
JSONDecoder = json.JSONDecoder


def dumps(
    obj: Any,
    default: Callable[[Any], Any] = to_primitive,
    **kwargs: Any,
) -> str:
    """Serialize ``obj`` to a JSON formatted ``str``.

    :param obj: object to be serialized
    :param default: function that returns a serializable version of an object,
        :func:`to_primitive` is used by default.
    :param kwargs: extra named parameters, please see documentation of
        `json.dumps <https://docs.python.org/3/library/json.html#basic-usage>`_
    :returns: json formatted string

    Use dump_as_bytes() to ensure that the result type is ``bytes``.
    """
    return json.dumps(obj, default=default, **kwargs)


def dump_as_bytes(
    obj: Any,
    default: Callable[[Any], Any] = to_primitive,
    encoding: str = 'utf-8',
    **kwargs: Any,
) -> bytes:
    """Serialize ``obj`` to a JSON formatted ``bytes``.

    :param obj: object to be serialized
    :param default: function that returns a serializable version of an object,
        :func:`to_primitive` is used by default.
    :param encoding: encoding used to encode the serialized JSON output
    :param kwargs: extra named parameters, please see documentation of
        `json.dumps <https://docs.python.org/3/library/json.html#basic-usage>`_
    :returns: json formatted string

    .. versionadded:: 1.10
    """
    return dumps(obj, default=default, **kwargs).encode(encoding)


def dump(obj: Any, fp: SupportsWrite, *args: Any, **kwargs: Any) -> None:
    """Serialize ``obj`` as a JSON formatted stream to ``fp``

    :param obj: object to be serialized
    :param fp: a ``.write()``-supporting file-like object
    :param default: function that returns a serializable version of an object,
        :func:`to_primitive` is used by default.
    :param args: extra arguments, please see documentation of
        `json.dump <https://docs.python.org/3/library/json.html#basic-usage>`_
    :param kwargs: extra named parameters, please see documentation of
        `json.dump <https://docs.python.org/3/library/json.html#basic-usage>`_

    .. versionchanged:: 1.3
       The *default* parameter now uses :func:`to_primitive` by default.
    """
    default = kwargs.get('default', to_primitive)
    return json.dump(obj, fp, default=default, *args, **kwargs)


def loads(s: str | bytes, encoding: str = 'utf-8', **kwargs: Any) -> Any:
    """Deserialize ``s`` (a ``str`` or ``unicode`` instance containing a JSON

    :param s: string to deserialize
    :param encoding: encoding used to interpret the string
    :param kwargs: extra named parameters, please see documentation of
        `json.loads <https://docs.python.org/3/library/json.html#basic-usage>`_
    :returns: python object
    """
    return json.loads(encodeutils.safe_decode(s, encoding), **kwargs)


def load(fp: ReadableStream, encoding: str = 'utf-8', **kwargs: Any) -> Any:
    """Deserialize ``fp`` to a Python object.

    :param fp: a ``.read()`` -supporting file-like object
    :param encoding: encoding used to interpret the string
    :param kwargs: extra named parameters, please see documentation of
        `json.loads <https://docs.python.org/3/library/json.html#basic-usage>`_
    :returns: python object
    """
    return json.load(codecs.getreader(encoding)(fp), **kwargs)
