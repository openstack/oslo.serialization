#    Copyright 2016 Mirantis, Inc.
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

from collections.abc import Callable
from typing import Any

from oslo_serialization import jsonutils
from oslo_serialization.serializer.base_serializer import BaseSerializer
from oslo_serialization._types import ReadableStream, SupportsWrite


class JSONSerializer(BaseSerializer):
    """JSON serializer based on the jsonutils module."""

    def __init__(
        self,
        default: Callable[[Any], Any] = jsonutils.to_primitive,
        encoding: str = 'utf-8',
    ) -> None:
        self._default = default
        self._encoding = encoding

    def dump(self, obj: Any, fp: SupportsWrite) -> None:
        return jsonutils.dump(obj, fp)

    def dump_as_bytes(self, obj: Any) -> bytes:
        return jsonutils.dump_as_bytes(
            obj, default=self._default, encoding=self._encoding
        )

    def load(self, fp: ReadableStream) -> Any:
        return jsonutils.load(fp, encoding=self._encoding)

    def load_from_bytes(self, s: bytes) -> Any:
        return jsonutils.loads(s, encoding=self._encoding)
